"""The relay between the ZMQ topics and the web console.

Out: every frame that a ZMQ publisher sends ("<topic> <compact JSON>") goes to every connected
console as one WebSocket text message, unchanged. The relay does not parse it.

Back: a console may send `command` frames. Each is checked (topic, JSON object, a string `type`)
and then published unchanged on a ZMQ PUB socket that this process binds, where vision listens.

A browser cannot speak ZMQ, and a page from any web site may open a WebSocket to this computer,
so the relay listens on this computer only and refuses pages that are not served from it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence

import zmq
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

COMMAND_TOPIC = "command"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# Where each topic's publisher binds (computer_vision/config.yaml, transport). Connecting to one
# that is not running yet is harmless: ZMQ keeps trying and the topic starts when it appears.
DEFAULT_SOURCES = (
    "tcp://127.0.0.1:5555",  # vision
    "tcp://127.0.0.1:5556",  # pressure
    "tcp://127.0.0.1:5558",  # robot
)
DEFAULT_COMMANDS = "tcp://127.0.0.1:5557"

# Pages served from this computer, on any port (the Vite dev server, a preview build).
LOCAL_PAGES = re.compile(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?")

# A command is a few dozen bytes. Anything much larger is not one.
MAX_COMMAND_BYTES = 4096
# Frames waiting for one console that reads too slowly. Beyond this the oldest is dropped:
# for live data, a late frame is worth less than the next one.
MAX_PENDING = 256
# A topic that sent nothing for this long is reported as silent.
SILENT_AFTER_S = 2.0
# More distinct topics than this are relayed but not tracked for the log.
MAX_TRACKED_TOPICS = 32

_POLL_MS = 100
# A console that does not answer the closing handshake (a frozen tab) is dropped after this long,
# so stopping the bridge never waits on it.
_CLOSE_TIMEOUT_S = 1.0


class RelayError(RuntimeError):
    """The relay could not start. The message says what to change."""


def parse_command(text: str) -> dict | None:
    """The payload of a well-formed command frame, or None if the frame is anything else."""
    topic, space, body = text.partition(" ")
    if topic != COMMAND_TOPIC or not space:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return None
    return payload


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


@dataclass
class Stats:
    frames: int = 0            # frames received from ZMQ and handed to the consoles
    commands: int = 0          # commands relayed to ZMQ
    rejected: int = 0          # console messages that were not well-formed commands
    unreadable: int = 0        # ZMQ frames that were not UTF-8 text
    dropped_slow: int = 0      # frames dropped for a console that read too slowly


@dataclass(eq=False)
class _Console:
    connection: ServerConnection
    pending: deque[str]
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    rejected: int = 0


class Relay:
    """Use as `async with Relay(...) as relay:`; it relays until the block is left."""

    def __init__(self, sources: Sequence[str] = DEFAULT_SOURCES, commands: str = DEFAULT_COMMANDS,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, extra_origins: Sequence[str] = (),
                 max_pending: int = MAX_PENDING, silent_after_s: float = SILENT_AFTER_S):
        self.sources = list(sources)
        self.host = host
        self.port = port                    # replaced by the real port once listening (matters for port 0)
        self.command_endpoint = commands    # replaced by the real endpoint once bound
        self.stats = Stats()
        # No Origin header means the client is not a browser (a script, a test). Such a client could
        # publish on ZMQ directly, so refusing it would protect nothing.
        self._origins: list = [LOCAL_PAGES, None, *extra_origins]
        self._max_pending = max_pending
        self._silent_after_s = silent_after_s

        self._consoles: set[_Console] = set()
        self._inbox: deque[str] = deque(maxlen=4 * max_pending)  # reader thread -> event loop
        self._drain_scheduled = False
        self._last_seen: dict[str, float] = {}
        self._flowing: set[str] = set()

        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ctx: zmq.Context | None = None
        self._pub: zmq.Socket | None = None
        self._server: Server | None = None
        self._reader: threading.Thread | None = None
        self._watcher: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def consoles(self) -> int:
        return len(self._consoles)

    # --- start and stop ---

    async def __aenter__(self) -> "Relay":
        self._loop = asyncio.get_running_loop()
        self._ctx = zmq.Context()
        try:
            self._pub = self._bind_commands()
            sub = self._connect_sources()
            try:
                self._server = await serve(self._handle, self.host, self.port, origins=self._origins,
                                           compression=None, max_size=MAX_COMMAND_BYTES,
                                           close_timeout=_CLOSE_TIMEOUT_S)
            except OSError as exc:
                sub.close(linger=0)
                raise RelayError(f"could not listen on {self.host}:{self.port} ({exc}). Another bridge may be "
                                 f"running; stop it, or choose another port with --listen.") from exc
        except BaseException:
            self._close_zmq()
            raise
        self.port = self._server.sockets[0].getsockname()[1]
        self._reader = threading.Thread(target=self._read, args=(sub,), name="bridge-zmq", daemon=True)
        self._reader.start()
        self._watcher = asyncio.create_task(self._watch_silence())
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._reader is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._reader.join)
        self._close_zmq()

    def _bind_commands(self) -> zmq.Socket:
        pub = self._ctx.socket(zmq.PUB)
        pub.set(zmq.LINGER, 0)
        try:
            pub.bind(self.command_endpoint)
        except zmq.ZMQError as exc:
            pub.close(linger=0)
            raise RelayError(f"could not bind the command publisher to {self.command_endpoint} ({exc}). Only one "
                             f"process can publish commands there; stop the other one, or pass --commands.") from exc
        self.command_endpoint = pub.last_endpoint.decode()
        return pub

    def _connect_sources(self) -> zmq.Socket:
        sub = self._ctx.socket(zmq.SUB)
        sub.set(zmq.LINGER, 0)
        sub.subscribe(b"")  # every topic: the consoles decide what they show
        for endpoint in self.sources:
            try:
                sub.connect(endpoint)
            except zmq.ZMQError as exc:
                sub.close(linger=0)
                raise RelayError(f"'{endpoint}' is not a ZMQ endpoint ({exc}). "
                                 f"Expected something like tcp://127.0.0.1:5555.") from exc
        return sub

    def _close_zmq(self) -> None:
        if self._pub is not None:
            self._pub.close(linger=0)
            self._pub = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    # --- ZMQ -> consoles ---

    def _read(self, sub: zmq.Socket) -> None:
        """Runs on its own thread, the only user of the SUB socket from here on."""
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        try:
            while not self._stop.is_set():
                if not poller.poll(_POLL_MS):
                    continue
                while True:
                    try:
                        raw = sub.recv(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    try:
                        self._inbox.append(raw.decode("utf-8"))
                    except UnicodeDecodeError:
                        self.stats.unreadable += 1
                # One wake-up for the whole batch. Setting the flag before scheduling, and clearing
                # it before draining, means a frame is never left behind without a wake-up.
                if not self._drain_scheduled:
                    self._drain_scheduled = True
                    try:
                        self._loop.call_soon_threadsafe(self._drain)
                    except RuntimeError:  # the event loop has closed
                        break
        finally:
            sub.close(linger=0)

    def _drain(self) -> None:
        self._drain_scheduled = False
        now = time.monotonic()
        while self._inbox:
            text = self._inbox.popleft()
            self.stats.frames += 1
            self._note_topic(text, now)
            for console in self._consoles:
                if len(console.pending) == console.pending.maxlen:
                    self.stats.dropped_slow += 1  # the append below pushes the oldest frame out
                console.pending.append(text)
                console.wake.set()

    def _note_topic(self, text: str, now: float) -> None:
        topic = text.partition(" ")[0]
        if topic not in self._last_seen and len(self._last_seen) >= MAX_TRACKED_TOPICS:
            return
        self._last_seen[topic] = now
        if topic not in self._flowing:
            self._flowing.add(topic)
            log.info("topic '%s' is flowing", topic)

    async def _watch_silence(self) -> None:
        while True:
            await asyncio.sleep(self._silent_after_s / 4)
            now = time.monotonic()
            for topic in [t for t in self._flowing if now - self._last_seen[t] > self._silent_after_s]:
                self._flowing.discard(topic)
                log.warning("topic '%s' went silent", topic)

    async def _write(self, console: _Console) -> None:
        """One per console. send() waits while that console's connection is full, and only that one."""
        try:
            while True:
                await console.wake.wait()
                console.wake.clear()
                while console.pending:
                    await console.connection.send(console.pending.popleft())
        except ConnectionClosed:
            pass

    # --- consoles -> ZMQ ---

    async def _handle(self, connection: ServerConnection) -> None:
        console = _Console(connection, deque(maxlen=self._max_pending))
        self._consoles.add(console)
        origin = connection.request.headers.get("Origin") if connection.request else None
        log.info("console connected from %s (%d connected)", origin or "a local program", len(self._consoles))
        writer = asyncio.create_task(self._write(console))
        try:
            async for message in connection:
                self._from_console(console, message)
        except ConnectionClosed:
            pass
        finally:
            self._consoles.discard(console)
            writer.cancel()
            log.info("console left (%d connected)", len(self._consoles))

    def _from_console(self, console: _Console, message: str | bytes) -> None:
        payload = parse_command(message) if isinstance(message, str) else None
        if payload is None:
            self.stats.rejected += 1
            console.rejected += 1
            # Say it once per console; a page that keeps sending it should not fill the log.
            log.log(logging.WARNING if console.rejected == 1 else logging.DEBUG,
                    "a console sent something that is not a command; dropped: %.80r", message)
            return
        try:
            self._pub.send(message.encode("utf-8"), zmq.NOBLOCK)
        except zmq.ZMQError as exc:
            log.warning("command '%s' could not be published: %s", payload["type"], exc)
            return
        self.stats.commands += 1
        log.info("command relayed: %s", message.partition(" ")[2])
