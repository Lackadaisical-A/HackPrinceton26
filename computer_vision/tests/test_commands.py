import logging
import socket
import time

import pytest
import zmq

from paul_vision.commands import CommandReceiver, apply_command
from paul_vision.messages import encode

CLASSES = ["cup", "bottle", "pill bottle"]
ANY_PORT = "tcp://127.0.0.1:0"


class Selector:
    """Stands in for Pipeline.set_preferred_label and records what it was asked."""

    def __init__(self):
        self.calls = []

    def __call__(self, label):
        self.calls.append(label)


# --- applying a command (pure) ---

def test_select_target_restricts_the_target_to_a_known_label():
    selector = Selector()
    assert apply_command({"t": 1.0, "type": "select_target", "label": "pill bottle"}, CLASSES, selector)
    assert selector.calls == ["pill bottle"]


@pytest.mark.parametrize("label", ["mug", "Cup", "", None, 3, ["cup"]])
def test_select_target_with_a_label_the_detector_does_not_know_is_ignored(label, caplog):
    selector = Selector()
    assert not apply_command({"type": "select_target", "label": label}, CLASSES, selector)
    assert selector.calls == []  # never a guess at what was meant
    assert "not one of detector.classes" in caplog.text


def test_select_target_without_a_label_is_ignored():
    selector = Selector()
    assert not apply_command({"type": "select_target"}, CLASSES, selector)
    assert selector.calls == []


def test_clear_target_lifts_the_restriction():
    selector = Selector()
    assert apply_command({"t": 1.0, "type": "clear_target"}, CLASSES, selector)
    assert selector.calls == [None]


@pytest.mark.parametrize("payload", [
    {"type": "set_phase", "phase": "grasp"},   # contract, but not built yet
    {"type": "set_haptics", "enabled": False},  # the console's; not for vision
    {"type": "self_destruct"},
    {"type": 5},
    {},
    None,
    "select_target",
    ["select_target", "cup"],
    7,
])
def test_anything_else_changes_nothing(payload):
    selector = Selector()
    assert not apply_command(payload, CLASSES, selector)
    assert selector.calls == []


def test_set_phase_is_reported_as_not_supported(caplog):
    apply_command({"type": "set_phase", "phase": "grasp"}, CLASSES, Selector())
    assert "not supported yet" in caplog.text


# --- the receiver, over real sockets ---

@pytest.fixture
def publisher():
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUB)
    sock.set(zmq.LINGER, 0)
    sock.bind(ANY_PORT)
    yield sock
    sock.close(linger=0)
    ctx.term()


def connect(publisher, receiver: CommandReceiver) -> None:
    """PUB/SUB drops messages until the subscription has reached the publisher."""
    deadline = time.monotonic() + 5
    while not receiver.poll():
        assert time.monotonic() < deadline, "the receiver never connected"
        publisher.send(encode("command", {"type": "clear_target"}))
        time.sleep(0.02)
    time.sleep(0.1)
    receiver.poll()  # drain


def poll_for(receiver: CommandReceiver, seconds: float) -> list:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        out.extend(receiver.poll())
        time.sleep(0.01)
    return out


def test_receiver_returns_the_commands_that_arrived(publisher):
    with CommandReceiver(publisher.last_endpoint.decode()) as receiver:
        connect(publisher, receiver)
        publisher.send(encode("command", {"t": 2.0, "type": "select_target", "label": "cup"}))
        publisher.send(encode("command", {"t": 2.1, "type": "clear_target"}))
        received = poll_for(receiver, 0.3)
    assert received == [{"t": 2.0, "type": "select_target", "label": "cup"}, {"t": 2.1, "type": "clear_target"}]


def test_receiver_ignores_other_topics_and_survives_unreadable_messages(publisher, caplog):
    with CommandReceiver(publisher.last_endpoint.decode()) as receiver:
        connect(publisher, receiver)
        caplog.set_level(logging.WARNING)
        publisher.send(encode("vision", {"type": "select_target", "label": "cup"}))
        publisher.send(b"command {not json")
        publisher.send(b"command \xff\xfe")
        publisher.send(b"commander {}")
        publisher.send(encode("command", {"type": "select_target", "label": "bottle"}))
        received = poll_for(receiver, 0.3)
    assert received == [{"type": "select_target", "label": "bottle"}]
    assert caplog.text.count("could not be read") == 2


def test_poll_returns_at_once_when_no_one_publishes():
    with socket.socket() as probe:  # a port that nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with CommandReceiver(f"tcp://127.0.0.1:{port}") as receiver:
        started = time.monotonic()
        for _ in range(1000):
            assert receiver.poll() == []
        assert time.monotonic() - started < 0.5


def test_a_flood_of_commands_is_taken_a_few_at_a_time(publisher):
    with CommandReceiver(publisher.last_endpoint.decode()) as receiver:
        connect(publisher, receiver)
        for i in range(100):
            publisher.send(encode("command", {"type": "clear_target", "n": i}))
        time.sleep(0.3)
        first = receiver.poll()
        assert 0 < len(first) <= 16  # one pass of the main loop never drains more than this
        rest = poll_for(receiver, 0.3)
    assert [c["n"] for c in first + rest] == list(range(100))


def test_a_malformed_endpoint_is_reported_clearly():
    with pytest.raises(RuntimeError, match="command subscriber"):
        CommandReceiver("not an endpoint")
