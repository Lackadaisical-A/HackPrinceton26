"""Contact from the gripper servo's load (`robot` topic): parsing, the receiver on real ZMQ, and the decision."""
import importlib.util
import json
import logging
import math
import time
from pathlib import Path

import pytest
import zmq

from paul_vision.config import GripperLoadConfig
from paul_vision.gripper_load import GripperLoadReceiver, GripReading, grip_level_of, reading_from_robot
from paul_vision.pressure import ContactDetector

ANY_PORT = "tcp://127.0.0.1:0"
TELEMETRY = Path(__file__).resolve().parents[2] / "leslider_control" / "telemetry.py"


def load_cfg(**changes) -> GripperLoadConfig:
    values = dict(enabled=True, sign=1, on_threshold=0.3, off_threshold=0.1, on_hold_s=0.2, off_hold_s=0.3,
                  stale_s=0.35, empty_pos=None, empty_margin=3.0)
    values.update(changes)
    return GripperLoadConfig(**values)


def robot(t=100.0, gripper_load=0.0, gripper_pos=50.0, feedback="measured", load="default"):
    """A `robot` payload as leslider_control/telemetry.robot_message builds it."""
    if load == "default":
        load = [0.2, -0.3, 0.1, 0.05, 0.0, gripper_load]
    return {"t": t, "pos": [0.0, 10.0, -20.0, 5.0, 0.0, gripper_pos], "load": load,
            "control": {"input": "potentiometer", "enabled": True, "input_fresh": True, "feedback": feedback,
                        "target": None}}


# --- reading_from_robot ---

def test_the_level_is_the_gripper_load_with_the_configured_sign_clipped_to_0_1():
    assert reading_from_robot(robot(gripper_load=0.4), load_cfg()) == GripReading(100.0, 0.4, 0.4, 50.0)
    assert reading_from_robot(robot(gripper_load=-0.4), load_cfg()).level == 0.0       # the other direction
    assert reading_from_robot(robot(gripper_load=-0.4), load_cfg(sign=-1)).level == 0.4
    assert reading_from_robot(robot(gripper_load=1.0), load_cfg()).level == 1.0
    assert reading_from_robot(robot(gripper_load=0), load_cfg()).level == 0.0           # an int, as JSON may send


def test_only_the_gripper_joint_counts():
    msg = robot(load=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0])
    assert reading_from_robot(msg, load_cfg()).level == 0.0


@pytest.mark.parametrize("msg", [
    robot(load=None),                                   # the bus read failed: position only
    robot(feedback="preview"),                          # a dry run: the targets, nothing measured
    robot(feedback="preview", load=None),               # what a dry run really sends
])
def test_a_message_without_a_measured_load_gives_no_reading(msg):
    assert reading_from_robot(msg, load_cfg()) is None


@pytest.mark.parametrize("msg, problem", [
    ([], "JSON object"),
    ({**robot(), "t": None}, "robot.t"),
    ({**robot(), "t": math.nan}, "robot.t"),
    ({**robot(), "t": True}, "robot.t"),
    ({k: v for k, v in robot().items() if k != "control"}, "robot.control"),
    ({**robot(), "pos": [0.0] * 5}, "robot.pos"),
    ({**robot(), "pos": "open"}, "robot.pos"),
    (robot(gripper_pos=math.inf), "robot.pos"),
    (robot(load=[0.0] * 7), "robot.load"),
    (robot(gripper_load=True), "robot.load"),
    (robot(gripper_load="0.4"), "robot.load"),
])
def test_a_broken_message_is_refused(msg, problem):
    with pytest.raises(ValueError, match=problem):
        reading_from_robot(msg, load_cfg())


def test_jaws_closed_on_nothing_read_as_no_grip():
    cfg = load_cfg(empty_pos=10.0, empty_margin=3.0)
    assert reading_from_robot(robot(gripper_load=0.6, gripper_pos=12.5), cfg).level == 0.0
    assert reading_from_robot(robot(gripper_load=0.6, gripper_pos=7.0), cfg).level == 0.0   # the margin is inclusive
    assert reading_from_robot(robot(gripper_load=0.6, gripper_pos=13.5), cfg).level == 0.6  # an object holds them apart
    assert reading_from_robot(robot(gripper_load=0.6, gripper_pos=12.5), load_cfg()).level == 0.6  # no check


def test_the_arm_controllers_own_message_is_read():
    # The contract with leslider_control/telemetry.py, checked against its code rather than a copy of it.
    if not TELEMETRY.exists():
        pytest.skip("leslider_control is not next to computer_vision")
    spec = importlib.util.spec_from_file_location("arm_telemetry", TELEMETRY)
    telemetry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(telemetry)
    positions = {name: 0.0 for name in telemetry.JOINTS} | {"gripper": 35.0}
    loads = {name: 0 for name in telemetry.JOINTS} | {"gripper": 420}       # raw Present_Load counts
    measured = telemetry.robot_message(positions, loads, targets=positions, enabled=True, input_fresh=True,
                                       preview=False, input_source="potentiometer", t=123.5)
    wire = json.loads(json.dumps(measured, separators=(",", ":"), allow_nan=False))
    assert reading_from_robot(wire, load_cfg()) == GripReading(123.5, 0.42, 0.42, 35.0)
    preview = telemetry.robot_message(positions, None, targets=positions, enabled=False, input_fresh=True,
                                      preview=True, input_source="potentiometer", t=124.0)
    assert reading_from_robot(preview, load_cfg()) is None


# --- the decision, at the arm controller's 10 Hz ---

def detector(**changes) -> ContactDetector:
    return ContactDetector(load_cfg(**changes), level_of=grip_level_of)


def reading(t, level):
    return GripReading(t=t, level=level, load=level, pos=40.0)


def test_contact_turns_on_and_off_after_the_hold_times():
    d = detector()
    states = [d.update(reading(t / 10, 0.5), t / 10).state for t in range(3)]
    assert states == [None, None, True]                             # on after 0.2 s: the third sample
    states = [d.update(reading(t / 10, 0.02), t / 10).state for t in range(3, 7)]
    assert states == [True, True, True, False]                      # off after 0.3 s: the fourth
    assert d.stale_s == 0.35


def test_a_level_between_the_thresholds_keeps_the_state():
    d = detector()
    for t in range(3):
        d.update(reading(t / 10, 0.5), t / 10)
    assert all(d.update(reading(t / 10, 0.2), t / 10).holding for t in range(3, 20))


def test_no_message_for_stale_s_makes_contact_unknown():
    d = detector()
    for t in range(3):
        d.update(reading(t / 10, 0.5), t / 10)
    assert d.update(None, 0.2 + 0.35).holding                       # stale_s after the last sample: still fresh
    late = d.update(None, 0.2 + 0.36)
    assert late.state is None and not late.fresh


def test_a_hole_in_the_feed_restarts_the_decision():
    d = detector()
    for t in range(3):
        d.update(reading(t / 10, 0.5), t / 10)
    # Two samples 0.5 s apart in one poll (the controller stalled): what happened in between is unknown.
    contact = d.update([reading(0.7, 0.5), reading(0.8, 0.5)], 0.8)
    assert contact.state is None and contact.fresh
    assert d.update(reading(0.9, 0.5), 0.9).holding                 # held again for 0.2 s


def test_the_controllers_jitter_is_not_a_hole():
    # Its loop also reads the bus, so samples arrive 0.1 to 0.3 s apart.
    d, t = detector(), 0.0
    for gap in [0.1, 0.3, 0.1, 0.25, 0.1, 0.3, 0.15]:
        t += gap
        contact = d.update(reading(t, 0.5), t)
    assert contact.holding


# --- the receiver, on real sockets ---

class RobotPublisher:
    """What the arm controller does: binds and sends `robot <json>`."""

    def __init__(self):
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.PUB)
        self.sock.set(zmq.LINGER, 0)
        self.sock.bind(ANY_PORT)
        self.endpoint = self.sock.last_endpoint.decode()

    def send(self, payload, topic="robot"):
        self.sock.send_string(f"{topic} {json.dumps(payload, separators=(',', ':'))}")

    def close(self):
        self.sock.close(linger=0)
        self.ctx.term()


@pytest.fixture
def feed():
    pub = RobotPublisher()
    receiver = GripperLoadReceiver(pub.endpoint, load_cfg())
    deadline = time.monotonic() + 5
    while receiver.poll() is None:  # PUB/SUB drops messages until the subscription has reached the publisher
        assert time.monotonic() < deadline, "the receiver never connected"
        pub.send(robot(t=1.0))
        time.sleep(0.02)
    time.sleep(0.1)
    receiver.poll()
    yield pub, receiver
    receiver.close()
    pub.close()


def test_the_receiver_hands_over_every_reading_since_the_last_poll(feed):
    pub, receiver = feed
    for i in range(5):
        pub.send(robot(t=10.0 + i / 10, gripper_load=i / 10))
    time.sleep(0.2)
    assert [(r.t, r.level) for r in receiver.poll()] == [(10.0 + i / 10, i / 10) for i in range(5)]
    assert receiver.poll() is None


def test_the_receiver_skips_messages_without_load_and_ignores_broken_ones(feed, caplog):
    pub, receiver = feed
    caplog.set_level(logging.WARNING)
    rejected, without = receiver.rejected, receiver.without_reading
    pub.send(robot(t=20.0, gripper_load=0.5))
    pub.send(robot(t=20.1, load=None))                  # no reading, and no warning: the bus read failed
    pub.send(robot(t=20.2, feedback="preview"))
    pub.send({**robot(t=20.3), "pos": [1.0]})          # broken
    pub.send(robot(t=20.4, gripper_load="0.5"))         # broken
    pub.sock.send(b"robot {not json")                   # broken
    pub.send(robot(t=20.5, gripper_load=0.9), topic="robots")  # another topic
    time.sleep(0.2)
    assert [r.t for r in receiver.poll()] == [20.0]
    assert receiver.without_reading - without == 2
    assert receiver.rejected - rejected == 3
    assert caplog.text.count("gripper load: ignored") == 1  # the first; the rest wait for the summary
