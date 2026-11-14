"""ZMQ transport: the vision publisher (with its stall watchdog) and a small subscriber helper."""
from __future__ import annotations

import logging
import queue
import threading

import zmq

from . import clock
from .config import WatchdogConfig
from .messages import TOPIC_VISION, build_vision_message, decode, encode
from .phase import PHASE_SEARCH
from .summary import TEXT_DEGRADED
from .types import STATUS_DEGRADED

log = logging.getLogger(__name__)

_STOP = object()


class Watchdog:
    """Decides when a `degraded` heartbeat is due. Pure logic: time comes in as an argument."""

    def __init__(self, cfg: WatchdogConfig, start_t: float):
        self._cfg = cfg
        self._last_fresh = start_t
        self._last_beat = start_t
        self._beats = 0  # heartbeats sent in the current stall; 0 = not stalled

    @property
    def stalled(self) -> bool:
        return self._beats > 0

    def feed(self, t: float) -> None:
        """A fresh message went out at time t."""
        self._last_fresh = t
        self._beats = 0

    def poll(self, t: float) -> int:
        """Returns 0 if no heartbeat is due, else its number within this stall (1 = stall just detected)."""
        if t < self._next_due():
            return 0
        self._beats += 1
        self._last_beat = t
        return self._beats

    def time_to_next(self, t: float) -> float:
        """Seconds until poll() can next return a heartbeat."""
        return max(0.0, self._next_due() - t)

    def _next_due(self) -> float:
        if self._beats:
            return self._last_beat + self._cfg.heartbeat_s
        return self._last_fresh + self._cfg.stall_timeout_s


class VisionPublisher:
    """Publishes vision messages from a dedicated thread, which is the only owner of the socket.

    The same thread runs the watchdog: if publish() is not called for `stall_timeout_s` (camera
    stalled, detector hung, pipeline paused), it sends `degraded` heartbeats by itself. It cannot
    report the whole process dying, so consumers must also treat silence as "no guidance".
    """

    def __init__(self, endpoint: str, send_hwm: int, watchdog: WatchdogConfig):
        self._bind_to = endpoint
        self._send_hwm = send_hwm
        self._watchdog_cfg = watchdog
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, send_hwm))
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="vision-pub", daemon=True)
        self.endpoint = endpoint  # replaced by the real endpoint once bound (matters for port 0)

    def start(self) -> "VisionPublisher":
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise RuntimeError(f"could not bind vision publisher to {self._bind_to}: {self._error}") from self._error
        log.info("publishing topic '%s' on %s", TOPIC_VISION, self.endpoint)
        return self

    def publish(self, message: dict) -> None:
        """Queue a message for sending. Never blocks; if the queue is full the oldest message is dropped."""
        if not self._thread.is_alive():
            raise RuntimeError("vision publisher thread is not running")
        while True:
            try:
                self._queue.put_nowait(message)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def close(self) -> None:
        if self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "VisionPublisher":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self) -> None:
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUB)
        try:
            sock.set(zmq.SNDHWM, self._send_hwm)
            sock.set(zmq.LINGER, 0)
            sock.bind(self._bind_to)
            self.endpoint = sock.last_endpoint.decode("utf-8")
        except Exception as exc:  # reported to the caller of start()
            self._error = exc
            sock.close(linger=0)
            ctx.term()
            self._ready.set()
            return
        self._ready.set()

        watchdog = Watchdog(self._watchdog_cfg, clock.now())
        last_frame_id = 0
        try:
            while True:
                try:
                    message = self._queue.get(timeout=max(0.001, watchdog.time_to_next(clock.now())))
                except queue.Empty:
                    message = None
                if message is _STOP:
                    break
                now = clock.now()
                if message is not None:
                    sock.send(encode(TOPIC_VISION, message))
                    if watchdog.stalled:
                        log.info("vision feed recovered")
                    watchdog.feed(now)
                    last_frame_id = message["frame_id"]
                    continue
                beat = watchdog.poll(now)
                if beat:
                    if beat == 1:
                        log.warning("no fresh vision message for %.2f s: publishing 'degraded'",
                                    self._watchdog_cfg.stall_timeout_s)
                    sock.send(encode(TOPIC_VISION, build_vision_message(
                        t=now, frame_id=last_frame_id, status=STATUS_DEGRADED, phase=PHASE_SEARCH,
                        summary=TEXT_DEGRADED if beat == 1 else None)))
        except Exception:
            log.exception("vision publisher thread crashed")
        finally:
            sock.close(linger=0)
            ctx.term()


class Subscriber:
    """Connects to a publisher and returns decoded (topic, payload) pairs.

    conflate=True keeps only the newest message, which is what a real-time consumer (haptics) wants.
    """

    def __init__(self, endpoint: str, topic: str = TOPIC_VISION, conflate: bool = False):
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.SUB)
        if conflate:
            self._sock.set(zmq.CONFLATE, 1)  # must be set before connect
        self._sock.set(zmq.LINGER, 0)
        self._sock.connect(endpoint)
        self._sock.subscribe(f"{topic} ".encode("utf-8"))

    def recv(self, timeout_s: float) -> tuple[str, dict] | None:
        """The next message, or None if nothing arrives within the timeout."""
        if not self._sock.poll(int(timeout_s * 1000)):
            return None
        return decode(self._sock.recv())

    def close(self) -> None:
        self._sock.close(linger=0)
        self._ctx.term()

    def __enter__(self) -> "Subscriber":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
