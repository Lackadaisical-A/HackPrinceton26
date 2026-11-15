"""Contact from the gripper servo's load: the grip signal while no pads publish `pressure`.

The arm controller (leslider_control/telemetry.py, RobotTelemetry) binds transport.robot_sub and publishes
`robot` at 10 Hz. Vision only subscribes; it never opens the servo bus. One message, compact JSON:

    robot {"t":1731542400.1,"pos":[6 values],"load":[6 values]|null,
           "control":{"input":...,"enabled":...,"input_fresh":...,"feedback":"measured"|"preview","target":...}}

Joints in the order of telemetry.JOINTS: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper.
`load` is the servo's Present_Load / 1000, clipped to -1..1; `t` is stamped (Unix time) just before the bus
read. The gripper position runs 0..100.

`reading_from_robot` turns one message into a GripReading whose level (0..1) is what ContactDetector compares
with the thresholds of the `gripper_load` section: sign * load of the gripper, clipped. A message with no load
(the bus read failed) or a dry run's preview gives no reading, so contact goes unknown after stale_s, as with
the pads: never holding, never let go.

A servo that holds an object presses against it, so its load rises; with jaws closed on nothing it can still
press one jaw on the other. `empty_pos` (the position of jaws closed on nothing) catches that case.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import GripperLoadConfig
from .messages import TOPIC_ROBOT, is_number
from .pressure import FeedReceiver

JOINT_COUNT = 6
GRIPPER = 5  # index of the gripper in `pos` and `load`
FEEDBACK_MEASURED = "measured"


@dataclass(frozen=True)
class GripReading:
    t: float        # sample time, Unix seconds, stamped by the arm controller
    level: float    # 0..1, what ContactDetector compares with the thresholds
    load: float     # the gripper's load as published, -1..1
    pos: float      # the gripper's position as published, 0..100


def _joint_values(msg: dict, key: str) -> list:
    values = msg.get(key)
    if not isinstance(values, list) or len(values) != JOINT_COUNT:
        raise ValueError(f"robot.{key} must be a list of {JOINT_COUNT} values")
    return values


def reading_from_robot(msg, cfg: GripperLoadConfig) -> GripReading | None:
    """The gripper's grip level from one `robot` payload, or None if the message carries no measured load.

    Raises ValueError if `msg` is not a `robot` message as telemetry.robot_message builds it.
    """
    if not isinstance(msg, dict):
        raise ValueError("robot message must be a JSON object")
    t = msg.get("t")
    if not is_number(t):
        raise ValueError("robot.t must be a finite number")
    control = msg.get("control")
    if not isinstance(control, dict):
        raise ValueError("robot.control must be an object")
    pos = _joint_values(msg, "pos")[GRIPPER]
    if not is_number(pos):
        raise ValueError("robot.pos: the gripper position must be a finite number")
    if msg.get("load") is None or control.get("feedback") != FEEDBACK_MEASURED:
        return None  # the bus read failed, or a dry run: nothing was measured
    load = _joint_values(msg, "load")[GRIPPER]
    if not is_number(load):
        raise ValueError("robot.load: the gripper load must be a finite number")
    level = min(1.0, max(0.0, cfg.sign * float(load)))
    if cfg.empty_pos is not None and abs(float(pos) - cfg.empty_pos) <= cfg.empty_margin:
        level = 0.0  # jaws closed on nothing, pressing on each other
    return GripReading(t=float(t), level=level, load=float(load), pos=float(pos))


class GripperLoadReceiver(FeedReceiver):
    """Reads `robot` messages as GripReadings for ContactDetector(cfg.gripper_load, level_of=grip_level_of)."""

    kind = "load"  # for the overlay's grip line

    def __init__(self, endpoint: str, cfg: GripperLoadConfig):
        super().__init__(endpoint, TOPIC_ROBOT, lambda msg: reading_from_robot(msg, cfg), "gripper load")


def grip_level_of(reading: GripReading) -> float:
    """ContactDetector's level_of for GripReadings."""
    return reading.level
