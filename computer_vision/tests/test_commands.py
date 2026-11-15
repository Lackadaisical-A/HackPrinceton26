import logging
import socket
import time

import pytest
import zmq

from paul_vision.commands import CommandHooks, CommandReceiver, LabelRequest, apply_command, parse_label_request
from paul_vision.messages import decode, encode
from paul_vision.phase import PHASES

CLASSES = ["cup", "bottle", "pill bottle"]
ANY_PORT = "tcp://127.0.0.1:0"
HUGE = "9" * 400  # a JSON number that is an int too large for a float: float() and math.isfinite() overflow on it
DEEP = 5000       # JSON nesting deep enough to make json.loads give up


class Recorder:
    """Stands in for a Pipeline method and records what it was asked."""

    def __init__(self):
        self.calls = []

    def __call__(self, value):
        self.calls.append(value)


class Hooks:
    """CommandHooks over recorders, so a test can see which hook a command called."""

    def __init__(self, with_phase: bool = True, with_aim: bool = True):
        self.selector, self.phases, self.aims = Recorder(), Recorder(), []
        self.hooks = CommandHooks(CLASSES, self.selector, self.phases if with_phase else None,
                                  set_aim=(lambda x, y: self.aims.append((x, y))) if with_aim else None)

    @property
    def calls(self) -> list:
        return self.selector.calls + self.phases.calls + self.aims


# --- applying a command (pure) ---

def test_select_target_restricts_the_target_to_a_known_label():
    h = Hooks()
    assert apply_command({"t": 1.0, "type": "select_target", "label": "pill bottle"}, h.hooks)
    assert h.selector.calls == ["pill bottle"] and h.phases.calls == []


@pytest.mark.parametrize("label", ["mug", "Cup", "", None, 3, ["cup"], "drop station"])
def test_select_target_with_a_label_the_detector_does_not_know_is_ignored(label, caplog):
    h = Hooks()
    assert not apply_command({"type": "select_target", "label": label}, h.hooks)
    assert h.calls == []  # never a guess at what was meant; a station is never a selectable object
    assert "not one of detector.classes" in caplog.text


def test_select_target_without_a_label_is_ignored():
    h = Hooks()
    assert not apply_command({"type": "select_target"}, h.hooks)
    assert h.calls == []


def test_clear_target_lifts_the_restriction():
    h = Hooks()
    assert apply_command({"t": 1.0, "type": "clear_target"}, h.hooks)
    assert h.selector.calls == [None]


@pytest.mark.parametrize("phase", PHASES)
def test_set_phase_sets_any_phase_of_the_contract(phase):
    h = Hooks()
    assert apply_command({"t": 1.0, "type": "set_phase", "phase": phase}, h.hooks)
    assert h.phases.calls == [phase] and h.selector.calls == []


@pytest.mark.parametrize("phase", ["Carry", "drop", "", None, 3, ["carry"]])
def test_set_phase_with_an_unknown_phase_is_ignored(phase, caplog):
    h = Hooks()
    assert not apply_command({"type": "set_phase", "phase": phase}, h.hooks)
    assert h.calls == []
    assert "is not one of search, approach, grasp, carry, place, release; ignored" in caplog.text


def test_set_phase_where_phases_are_not_run_is_ignored(caplog):
    h = Hooks(with_phase=False)
    assert not apply_command({"type": "set_phase", "phase": "carry"}, h.hooks)
    assert h.calls == []
    assert "phases are not run here" in caplog.text


@pytest.mark.parametrize("x, y", [(0.52, 0.61), (0, 1), (1.0, 0.0), (0.5, 0.5)])
def test_set_aim_moves_the_aim_point(x, y, caplog):
    caplog.set_level(logging.INFO)
    h = Hooks()
    assert apply_command({"t": 1.0, "type": "set_aim", "x": x, "y": y}, h.hooks)
    assert h.aims == [(x, y)] and h.selector.calls == [] and h.phases.calls == []
    assert all(type(v) is float for v in h.aims[0])
    assert "aim set to" in caplog.text


@pytest.mark.parametrize("payload", [
    {"type": "set_aim", "x": 1.2, "y": 0.5},
    {"type": "set_aim", "x": 0.5, "y": -0.01},
    {"type": "set_aim", "x": "0.5", "y": 0.5},
    {"type": "set_aim", "x": True, "y": 0.5},     # a bool is not a number here
    {"type": "set_aim", "x": 0.5},
    {"type": "set_aim", "x": [0.5], "y": 0.5},
    {"type": "set_aim", "x": float("nan"), "y": 0.5},
    {"type": "set_aim", "x": int(HUGE), "y": 0.5},
])
def test_set_aim_off_the_picture_or_not_a_number_is_ignored(payload, caplog):
    h = Hooks()
    assert not apply_command(payload, h.hooks)
    assert h.calls == []
    assert "x and y must be numbers from 0 to 1" in caplog.text


def test_set_aim_where_guidance_is_not_run_is_ignored(caplog):
    h = Hooks(with_aim=False)
    assert not apply_command({"type": "set_aim", "x": 0.5, "y": 0.5}, h.hooks)
    assert h.calls == []
    assert "guidance is not run here" in caplog.text


@pytest.mark.parametrize("payload", [
    {"type": "set_haptics", "enabled": False},  # the console's; not for vision
    {"type": "tag_text", "name": "my mug"},     # no teach hook here: labels cannot change
    {"type": "self_destruct"},
    {"type": 5},
    {},
    None,
    "select_target",
    ["select_target", "cup"],
    7,
])
def test_anything_else_changes_nothing(payload):
    h = Hooks()
    assert not apply_command(payload, h.hooks)
    assert h.calls == []


# --- label commands (M4): read here, judged by the label manager ---

def test_select_target_takes_the_live_tag_names():
    live = ["cup", "bottle"]
    selector = Recorder()
    hooks = CommandHooks(lambda: live, selector)
    assert not apply_command({"type": "select_target", "label": "my flask"}, hooks)
    live.append("my flask")  # the tag went live: the next command sees it
    assert apply_command({"type": "select_target", "label": "my flask"}, hooks)
    assert selector.calls == ["my flask"]


@pytest.mark.parametrize("payload, expected", [
    ({"t": 1.0, "type": "tag_visual", "name": "  My   Flask ", "bbox": [0.23, 0.52, 0.3, 0.72]},
     LabelRequest("tag_visual", "my flask", bbox=(0.23, 0.52, 0.3, 0.72))),
    ({"type": "tag_visual", "name": "my flask", "bbox": None}, LabelRequest("tag_visual", "my flask")),
    ({"type": "tag_visual", "name": "my flask"}, LabelRequest("tag_visual", "my flask")),
    ({"type": "tag_visual", "name": "my flask", "bbox": [0, 0, 1, 1]},
     LabelRequest("tag_visual", "my flask", bbox=(0.0, 0.0, 1.0, 1.0))),
    ({"type": "tag_text", "name": "my mug", "prompt": " red   mug "}, LabelRequest("tag_text", "my mug", prompt="red mug")),
    ({"type": "tag_text", "name": "My Mug"}, LabelRequest("tag_text", "my mug", prompt="my mug")),
    ({"type": "untag", "name": "my flask"}, LabelRequest("untag", "my flask")),
    ({"type": "untag"}, LabelRequest("untag", "")),
    ({"type": "tag_text", "name": 5}, LabelRequest("tag_text", "", prompt="")),
])
def test_label_payloads_are_read(payload, expected):
    assert parse_label_request(payload) == expected


@pytest.mark.parametrize("payload, problem", [
    ({"type": "tag_visual", "name": "my flask", "bbox": [0.2, 0.5, 0.3]}, "Cannot learn my flask: the box is not valid."),
    ({"type": "tag_visual", "name": "my flask", "bbox": "0.2,0.5,0.3,0.7"}, "the box is not valid."),
    ({"type": "tag_visual", "name": "my flask", "bbox": [0.2, 0.5, True, 0.7]}, "the box is not valid."),
    ({"type": "tag_visual", "name": "my flask", "bbox": [0.2, 0.5, float("nan"), 0.7]}, "the box is not valid."),
    ({"type": "tag_visual", "bbox": [0.2, 0.5, 0.3]}, "Cannot learn that: the box is not valid."),
    ({"type": "tag_text", "name": "my mug", "prompt": "  "}, "Cannot learn my mug: the description is empty."),
    ({"type": "tag_text", "name": "my mug", "prompt": 3}, "the description is empty."),
])
def test_unusable_label_payloads_carry_a_sentence(payload, problem):
    assert parse_label_request(payload).problem.endswith(problem)


def test_label_commands_go_to_the_teach_hook():
    asked = []
    hooks = CommandHooks(CLASSES, Recorder(), teach=lambda req: asked.append(req) or True)
    assert apply_command({"type": "tag_text", "name": "my mug"}, hooks)
    assert apply_command({"type": "untag", "name": "my mug"}, hooks)
    bad = {"type": "tag_visual", "name": "x", "bbox": [1, 2]}
    apply_command(bad, hooks)  # an unusable one too: the manager answers it with a sentence
    assert [r.kind for r in asked] == ["tag_text", "untag", "tag_visual"] and asked[2].problem


def test_label_commands_without_a_teach_hook_are_ignored(caplog):
    h = Hooks()
    assert not apply_command({"type": "untag", "name": "my mug"}, h.hooks)
    assert "labels cannot change here" in caplog.text


# --- payloads that once stopped vision ---

def test_a_box_with_a_number_too_large_for_a_float_is_not_valid():
    _, payload = decode(b'command {"type":"tag_visual","name":"my flask","bbox":[0,0,%s,1]}' % HUGE.encode())
    assert parse_label_request(payload).problem == "Cannot learn my flask: the box is not valid."
    asked = []
    hooks = CommandHooks(CLASSES, Recorder(), teach=lambda req: asked.append(req) or True)
    apply_command(payload, hooks)  # used to raise OverflowError
    assert len(asked) == 1 and asked[0].bbox is None and asked[0].problem.endswith("the box is not valid.")


def test_a_command_nested_too_deeply_never_reaches_apply_command():
    body = b'{"type":"select_target","label":' + b"[" * DEEP + b"]" * DEEP + b"}"
    with pytest.raises(ValueError, match="nested too deeply"):  # was RecursionError, which poll did not catch
        decode(b"command " + body)


def test_a_deeply_nested_value_that_json_still_reads_is_ignored():
    depth = 2000  # json.loads reads this; the label is a list, so it is not one of the labels
    _, payload = decode(b'command {"type":"select_target","label":' + b"[" * depth + b"]" * depth + b"}")
    h = Hooks()
    assert not apply_command(payload, h.hooks) and h.calls == []


@pytest.mark.parametrize("payload", [{"type": "select_target", "label": "cup"}, {"type": "clear_target"},
                                     {"type": "set_phase", "phase": "carry"}, {"type": "untag", "name": "my mug"}])
def test_apply_command_never_raises_when_acting_fails(payload, caplog):
    def broken(_value):
        raise RuntimeError("the hook failed")
    hooks = CommandHooks(CLASSES, broken, set_phase=broken, teach=broken)
    assert apply_command(payload, hooks) is False
    assert caplog.text.count("could not be applied; ignored") == 1 and repr(payload["type"]) in caplog.text


def test_apply_command_never_raises_when_the_labels_cannot_be_read(caplog):
    def no_labels():
        raise OSError("library.json is locked")
    hooks = CommandHooks(no_labels, Recorder())
    assert apply_command({"type": "select_target", "label": "cup"}, hooks) is False
    assert "command of type 'select_target' could not be applied; ignored" in caplog.text


@pytest.mark.parametrize("payload", [None, [], "select_target", 5, 1.5, True, {"type": ["select_target"]},
                                     {"type": {"a": 1}}, {"type": None}, {}])
def test_apply_command_ignores_payloads_that_are_not_commands(payload):
    h = Hooks()
    assert apply_command(payload, h.hooks) is False and h.calls == []


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


def test_receiver_survives_deep_nesting_and_huge_numbers(publisher, caplog):
    with CommandReceiver(publisher.last_endpoint.decode()) as receiver:
        connect(publisher, receiver)
        caplog.set_level(logging.WARNING)
        publisher.send(b'command {"type":"select_target","label":' + b"[" * DEEP + b"]" * DEEP + b"}")
        publisher.send(b"command " + b"[" * DEEP)  # unclosed as well
        huge = b'command {"type":"tag_visual","name":"my flask","bbox":[0,0,%s,1]}' % HUGE.encode()
        publisher.send(huge)
        publisher.send(encode("command", {"type": "select_target", "label": "bottle"}))
        received = poll_for(receiver, 0.3)
    assert caplog.text.count("could not be read") == 2
    assert received[0]["type"] == "tag_visual" and received[1:] == [{"type": "select_target", "label": "bottle"}]
    asked = []
    hooks = CommandHooks(CLASSES, Recorder(), teach=lambda req: asked.append(req) or True)
    for payload in received:
        apply_command(payload, hooks)
    assert asked[0].problem == "Cannot learn my flask: the box is not valid."


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


def test_a_malformed_endpoint_among_several_is_reported_clearly():
    with pytest.raises(RuntimeError, match="command subscriber"):
        CommandReceiver(["tcp://127.0.0.1:1", "not an endpoint"])


@pytest.fixture
def publishers():
    """Three publishers on free ports, as the console bridge, voice and send_command each bind their own."""
    ctx = zmq.Context()
    socks = []
    for _ in range(3):
        sock = ctx.socket(zmq.PUB)
        sock.set(zmq.LINGER, 0)
        sock.bind(ANY_PORT)
        socks.append(sock)
    yield socks
    for sock in socks:
        sock.close(linger=0)
    ctx.term()


def test_receiver_takes_commands_from_every_endpoint(publishers):
    endpoints = [p.last_endpoint.decode() for p in publishers]
    with CommandReceiver(endpoints) as receiver:
        assert receiver.endpoints == endpoints
        for p in publishers:
            connect(p, receiver)
        for i, p in enumerate(publishers):
            p.send(encode("command", {"type": "set_phase", "phase": PHASES[i], "n": i}))
        received = poll_for(receiver, 0.3)
    assert sorted(c["n"] for c in received) == [0, 1, 2]  # fair-queued: the order between senders is not kept


def test_a_sender_that_is_not_running_does_not_hold_up_the_others(publishers):
    with socket.socket() as probe:  # nothing listens here: like voice, which is not built yet
        probe.bind(("127.0.0.1", 0))
        silent = f"tcp://127.0.0.1:{probe.getsockname()[1]}"
    with CommandReceiver([silent, publishers[0].last_endpoint.decode()]) as receiver:
        connect(publishers[0], receiver)
        publishers[0].send(encode("command", {"type": "set_phase", "phase": "carry"}))
        assert poll_for(receiver, 0.3) == [{"type": "set_phase", "phase": "carry"}]
