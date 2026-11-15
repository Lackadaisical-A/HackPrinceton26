"""The relay over real sockets: a ZMQ publisher on one side, WebSocket clients on the other."""
import argparse
import asyncio
import logging
import time

import pytest
import zmq
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from paul_bridge.__main__ import parse_args, parse_listen
from paul_bridge.relay import (DEFAULT_COMMANDS, DEFAULT_SOURCES, LOCAL_PAGES, Relay, RelayError, is_loopback,
                               parse_command)

ANY_PORT = "tcp://127.0.0.1:0"
VISION = ('vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{"track_id":7,'
          '"label":"cup","source":"yolo","bbox":[0.41,0.3,0.62,0.58],"confidence":0.91},"guidance":{"dx":-0.12,'
          '"dy":0.05,"distance_hint":"near","aligned":false},"summary":"Cup near, slightly left."}')
SELECT = 'command {"t":1731542401.5,"type":"select_target","label":"pill bottle"}'
WARM_UP = "warmup {}"


@pytest.fixture
def zmq_ctx():
    ctx = zmq.Context()
    yield ctx
    ctx.destroy(linger=0)


@pytest.fixture
def publisher(zmq_ctx):
    """Stands in for the vision process."""
    sock = zmq_ctx.socket(zmq.PUB)
    sock.bind(ANY_PORT)
    return sock


def endpoint_of(sock: zmq.Socket) -> str:
    return sock.last_endpoint.decode()


def relay_for(publisher=None, **kwargs) -> Relay:
    sources = [endpoint_of(publisher)] if publisher is not None else []
    return Relay(sources, ANY_PORT, port=0, **kwargs)


async def next_text(client, timeout: float = 3.0) -> str:
    return await asyncio.wait_for(client.recv(), timeout)


async def join(publisher, client) -> None:
    """PUB/SUB drops frames until the relay's subscription has reached the publisher."""
    deadline = time.monotonic() + 5
    while True:
        assert time.monotonic() < deadline, "nothing came through the relay"
        publisher.send_string(WARM_UP)
        try:
            await next_text(client, 0.05)
            break
        except TimeoutError:
            pass
    await drain(client)


async def drain(client, quiet_s: float = 0.15) -> list[str]:
    seen = []
    while True:
        try:
            seen.append(await next_text(client, quiet_s))
        except TimeoutError:
            return seen


def command_listener(zmq_ctx, relay: Relay) -> zmq.Socket:
    """Stands in for vision's command receiver."""
    sub = zmq_ctx.socket(zmq.SUB)
    sub.subscribe(b"command")
    sub.connect(relay.command_endpoint)
    return sub


async def received(sub: zmq.Socket, seconds: float) -> list[str]:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            out.append(sub.recv(zmq.NOBLOCK).decode())
        except zmq.Again:
            await asyncio.sleep(0.01)
    return out


async def join_commands(client, sub: zmq.Socket) -> None:
    deadline = time.monotonic() + 5
    while not await received(sub, 0.05):
        assert time.monotonic() < deadline, "no command came through the relay"
        await client.send('command {"type":"clear_target"}')
    await received(sub, 0.15)


# --- ZMQ to the console ---

def test_a_frame_reaches_the_console_exactly_as_it_was_published(publisher):
    async def scenario():
        async with relay_for(publisher) as relay, connect(relay.url) as client:
            await join(publisher, client)
            publisher.send_string(VISION)
            publisher.send_string('pressure {"t":1.5,"norm":[0.1,0.2,0.3,0.4]}')
            assert await next_text(client) == VISION
            assert await next_text(client) == 'pressure {"t":1.5,"norm":[0.1,0.2,0.3,0.4]}'
            assert relay.stats.frames >= 3

    asyncio.run(scenario())


def test_every_console_gets_every_frame_in_order(publisher):
    async def scenario():
        async with relay_for(publisher) as relay, connect(relay.url) as first, connect(relay.url) as second:
            await join(publisher, first)
            await drain(second)
            assert relay.consoles == 2
            frames = [f'vision {{"frame_id":{i}}}' for i in range(200)]
            for frame in frames:
                publisher.send_string(frame)
            assert [await next_text(first) for _ in frames] == frames
            assert [await next_text(second) for _ in frames] == frames

    asyncio.run(scenario())


def test_a_frame_that_is_not_text_is_dropped_and_the_next_one_arrives(publisher):
    async def scenario():
        async with relay_for(publisher) as relay, connect(relay.url) as client:
            await join(publisher, client)
            publisher.send(b"vision \xff\xfe\xfd")
            publisher.send_string(VISION)
            assert await next_text(client) == VISION
            assert relay.stats.unreadable == 1

    asyncio.run(scenario())


def test_several_publishers_are_relayed_and_one_that_is_not_running_does_no_harm(zmq_ctx, publisher):
    other = zmq_ctx.socket(zmq.PUB)
    other.bind(ANY_PORT)
    nobody = "tcp://127.0.0.1:9"  # nothing listens here

    async def scenario():
        async with Relay([endpoint_of(publisher), nobody, endpoint_of(other)], ANY_PORT, port=0) as relay, \
                connect(relay.url) as client:
            await join(publisher, client)
            await join(other, client)
            publisher.send_string(VISION)
            other.send_string('robot {"t":2.0}')
            assert sorted([await next_text(client), await next_text(client)]) == ['robot {"t":2.0}', VISION]

    asyncio.run(scenario())


def test_a_console_that_stops_reading_loses_old_frames_and_holds_no_one_else_up(publisher):
    big = "vision " + "x" * 60_000

    async def scenario():
        async with relay_for(publisher, max_pending=8) as relay, connect(relay.url) as healthy, \
                connect(relay.url, max_queue=1, close_timeout=0.2) as stalled:  # never read from, so it fills up
            await join(publisher, healthy)
            deadline = time.monotonic() + 20
            while relay.stats.dropped_slow == 0:
                assert time.monotonic() < deadline, "the stalled console never filled up"
                publisher.send_string(big)
                assert await next_text(healthy) == big
            # What waits for the stalled console is bounded, and the healthy one still gets fresh frames at once.
            assert all(len(c.pending) <= 8 for c in relay._consoles)
            publisher.send_string(VISION)
            assert await next_text(healthy, 1.0) == VISION
            del stalled

    asyncio.run(scenario())


def test_a_topic_is_reported_when_it_starts_and_when_it_goes_silent(publisher, caplog):
    caplog.set_level(logging.INFO, logger="paul_bridge.relay")

    async def scenario():
        async with relay_for(publisher, silent_after_s=0.2) as relay, connect(relay.url) as client:
            await join(publisher, client)
            for _ in range(5):
                publisher.send_string(VISION)
                await asyncio.sleep(0.02)
            assert "topic 'vision' is flowing" in caplog.text
            assert "topic 'vision' went silent" not in caplog.text
            await asyncio.sleep(0.5)
            assert "topic 'vision' went silent" in caplog.text
            await drain(client)  # the frames above, so that the next one read is the one sent next
            publisher.send_string(VISION)
            await next_text(client)
            assert caplog.text.count("topic 'vision' is flowing") == 2
            assert relay.consoles == 1

    asyncio.run(scenario())


# --- commands from the console ---

def test_a_command_reaches_zmq_exactly_as_the_console_sent_it(zmq_ctx):
    async def scenario():
        async with relay_for() as relay, connect(relay.url, origin="http://localhost:5173") as client:
            sub = command_listener(zmq_ctx, relay)
            await join_commands(client, sub)
            before = relay.stats.commands
            await client.send(SELECT)
            assert await received(sub, 0.3) == [SELECT]
            assert relay.stats.commands == before + 1

    asyncio.run(scenario())


def test_what_is_not_a_command_never_reaches_zmq(zmq_ctx, caplog):
    async def scenario():
        async with relay_for() as relay, connect(relay.url) as client:
            sub = zmq_ctx.socket(zmq.SUB)
            sub.subscribe(b"")  # everything the relay publishes, whatever the topic
            sub.connect(relay.command_endpoint)
            await join_commands(client, sub)
            for message in [VISION,                              # a console may not forge another topic
                            'robot {"type":"move","joint":2}',
                            "command",
                            "command not json",
                            'command ["select_target","cup"]',
                            'command {"label":"cup"}',
                            'command {"type":5}',
                            'commander {"type":"clear_target"}',
                            b'command {"type":"clear_target"}',   # binary
                            ""]:
                await client.send(message)
            await client.send(SELECT)
            assert await received(sub, 0.3) == [SELECT]
            assert relay.stats.rejected == 10
        assert caplog.text.count("not a command") == 1  # said once per console, not per message

    asyncio.run(scenario())


def test_an_oversized_message_closes_that_console_and_reaches_no_one(zmq_ctx):
    async def scenario():
        async with relay_for() as relay, connect(relay.url) as client:
            sub = command_listener(zmq_ctx, relay)
            await join_commands(client, sub)
            await client.send('command {"type":"select_target","label":"' + "a" * 10_000 + '"}')
            with pytest.raises(ConnectionClosed):
                await next_text(client)
            assert await received(sub, 0.2) == []
            async with connect(relay.url) as again:  # the relay itself is unharmed
                await again.send(SELECT)
                assert await received(sub, 0.3) == [SELECT]

    asyncio.run(scenario())


@pytest.mark.parametrize("text, payload", [
    ('command {"type":"clear_target"}', {"type": "clear_target"}),
    (SELECT, {"t": 1731542401.5, "type": "select_target", "label": "pill bottle"}),
    ('command  {"type": "set_haptics", "enabled": false}', {"type": "set_haptics", "enabled": False}),
])
def test_parse_command_accepts_a_command(text, payload):
    assert parse_command(text) == payload


@pytest.mark.parametrize("text", [
    "", "command", "command ", "command {", "command null", "command 3", 'command "clear_target"',
    'command {"type":null}', 'command {"Type":"clear_target"}', ' command {"type":"clear_target"}',
    'Command {"type":"clear_target"}', 'vision {"type":"clear_target"}', '{"type":"clear_target"}',
])
def test_parse_command_refuses_everything_else(text):
    assert parse_command(text) is None


# --- who may connect ---

@pytest.mark.parametrize("origin", [
    "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "https://localhost", "http://[::1]:5173",
    None,  # not a browser
])
def test_pages_served_from_this_computer_may_connect(origin):
    async def scenario():
        async with relay_for() as relay, connect(relay.url, origin=origin):
            await asyncio.sleep(0.05)
            assert relay.consoles == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", [
    "https://attacker.example",
    "http://localhost.attacker.example",
    "http://127.0.0.1.attacker.example:5173",
    "http://attacker.example/?http://localhost:5173",
    "null",                       # a page opened from a file, or a sandboxed frame
    "http://192.168.1.20:5173",   # another computer, unless it was allowed
])
def test_pages_from_anywhere_else_are_refused(origin):
    async def scenario():
        async with relay_for() as relay:
            with pytest.raises(InvalidStatus) as refused:
                async with connect(relay.url, origin=origin):
                    pass
            assert refused.value.response.status_code == 403
            assert relay.consoles == 0

    asyncio.run(scenario())


def test_an_extra_origin_can_be_allowed():
    async def scenario():
        async with relay_for(extra_origins=["http://192.168.1.20:5173"]) as relay:
            async with connect(relay.url, origin="http://192.168.1.20:5173"):
                pass
            with pytest.raises(InvalidStatus):
                async with connect(relay.url, origin="http://192.168.1.21:5173"):
                    pass

    asyncio.run(scenario())


def test_local_pages_pattern_matches_whole_origins_only():
    assert LOCAL_PAGES.fullmatch("http://localhost:5173")
    assert not LOCAL_PAGES.fullmatch("http://localhost:5173.attacker.example")
    assert not LOCAL_PAGES.fullmatch("ftp://localhost")


# --- starting and stopping ---

def test_the_defaults_match_the_rest_of_the_system():
    relay = Relay()
    assert (relay.host, relay.port, relay.url) == ("127.0.0.1", 8765, "ws://127.0.0.1:8765")
    assert relay.sources == ["tcp://127.0.0.1:5555", "tcp://127.0.0.1:5556", "tcp://127.0.0.1:5558", "tcp://127.0.0.1:5561", "tcp://127.0.0.1:5562", "tcp://127.0.0.1:5563"]
    assert relay.command_endpoint == "tcp://127.0.0.1:5557" == DEFAULT_COMMANDS
    assert all(endpoint.startswith("tcp://127.0.0.1:") for endpoint in DEFAULT_SOURCES)


def test_a_websocket_port_that_is_taken_is_reported_and_leaves_nothing_behind():
    async def scenario():
        async with relay_for() as first:
            with pytest.raises(RelayError, match="could not listen"):
                async with Relay([], ANY_PORT, port=first.port):
                    pass
        async with relay_for():  # and the failed start did not keep a socket or a thread
            pass

    asyncio.run(scenario())


def test_a_command_endpoint_that_is_taken_is_reported():
    async def scenario():
        async with relay_for() as first:
            with pytest.raises(RelayError, match="command publisher"):
                async with Relay([], first.command_endpoint, port=0):
                    pass

    asyncio.run(scenario())


def test_a_malformed_source_is_reported():
    async def scenario():
        with pytest.raises(RelayError, match="not a ZMQ endpoint"):
            async with Relay(["127.0.0.1:5555"], ANY_PORT, port=0):
                pass

    asyncio.run(scenario())


def test_stopping_closes_the_consoles_and_frees_the_ports(publisher):
    async def scenario():
        async with relay_for(publisher) as relay:
            port, commands = relay.port, relay.command_endpoint
            client = await connect(relay.url)
            await join(publisher, client)
        with pytest.raises(ConnectionClosed):
            await next_text(client)
        async with Relay([endpoint_of(publisher)], commands, port=port) as again, connect(again.url) as client:
            await join(publisher, client)

    asyncio.run(scenario())


# --- the command line ---

def test_listen_argument():
    assert parse_listen("127.0.0.1:8765") == ("127.0.0.1", 8765)
    assert parse_listen("0.0.0.0:9000") == ("0.0.0.0", 9000)
    assert parse_listen("9000") == ("127.0.0.1", 9000)
    assert parse_listen("[::1]:8765") == ("::1", 8765)
    for bad in ("localhost", "127.0.0.1:", "127.0.0.1:http", "127.0.0.1:70000"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_listen(bad)


def test_command_line_defaults_and_overrides():
    args = parse_args([])
    assert args.listen == ("127.0.0.1", 8765) and args.sources is None and args.origins == []
    assert args.commands == "tcp://127.0.0.1:5557"
    args = parse_args(["--listen", "8770", "--from", "tcp://127.0.0.1:6000", "--from", "tcp://127.0.0.1:6001",
                       "--commands", "tcp://127.0.0.1:6002", "--origin", "http://192.168.1.20:5173", "--verbose"])
    assert args.listen == ("127.0.0.1", 8770) and args.verbose
    assert args.sources == ["tcp://127.0.0.1:6000", "tcp://127.0.0.1:6001"]
    assert args.commands == "tcp://127.0.0.1:6002" and args.origins == ["http://192.168.1.20:5173"]


def test_only_this_computer_counts_as_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.20")
