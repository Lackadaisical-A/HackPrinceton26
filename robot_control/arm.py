"""Adapter around the copied LeRobot Feetech driver. Opening only reads motors."""
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/"vendor"))

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
BODY = JOINTS[:-1]


def choose_port(requested=None, excluded=("COM7", "COM8")):
    from serial.tools.list_ports import comports
    ports = list(comports())
    excluded = {p.upper() for p in excluded}
    if requested:
        if requested.upper() in excluded:
            raise ValueError(f"{requested} belongs to an ESP32, not the arm")
        if requested.upper() not in {p.device.upper() for p in ports}:
            raise ValueError(f"{requested} is not attached. Present: {', '.join(p.device for p in ports)}")
        return requested
    candidates = [p.device for p in ports if p.vid is not None and p.device.upper() not in excluded]
    if len(candidates) != 1:
        raise ValueError("Arm USB port not uniquely detected. "
                         f"Candidates: {candidates or 'none'}. Present: {[p.device for p in ports]}. "
                         "Connect the arm USB controller, then specify -RobotPort COMx if needed.")
    return candidates[0]


class Arm:
    def __init__(self, port, calibration=HERE/"calibration.json"):
        values = json.loads(Path(calibration).read_text())
        if set(values) != set(JOINTS):
            raise ValueError("Expected saved calibration for the six SO-101 joints")
        self.calibration = {name: MotorCalibration(**values[name]) for name in JOINTS}
        for index, name in enumerate(JOINTS, 1):
            c = self.calibration[name]
            if c.id != index or not 0 <= c.range_min < c.range_max <= 4095:
                raise ValueError(f"Invalid calibration: {name}")
        self.bus = FeetechMotorsBus(port, {
            name: Motor(c.id, "sts3215", MotorNormMode.DEGREES)
            for name, c in self.calibration.items()
        }, calibration=self.calibration)
        self.commanded = False
        self.gripper_commanded = False

    def connect(self):
        self.bus.connect()  # Ping/model/firmware checks; no motor configuration writes.

    def close(self):
        if self.bus.is_connected:
            self.bus.disconnect(disable_torque=False)

    def read_load(self, joint):
        # LeRobot already decodes the sign-magnitude bit at index 10.
        return int(self.bus.read("Present_Load", joint, normalize=False, num_retry=0))

    def positions(self):
        return self.bus.sync_read("Present_Position", list(BODY), normalize=False, num_retry=0)

    def validate_motion(self):
        return self._validate_joints(BODY)

    def _validate_joints(self, joints):
        current = self.bus.sync_read("Present_Position", list(joints), normalize=False, num_retry=0)
        for name in joints:
            c = self.calibration[name]
            homing = self.bus.read("Homing_Offset", name, normalize=False)
            mode = self.bus.read("Operating_Mode", name, normalize=False)
            if homing != c.homing_offset or mode != 0:
                raise ValueError(f"{name}: saved calibration/mode does not match hardware; motion refused")
            if not c.range_min <= current[name] <= c.range_max:
                raise ValueError(f"{name}: position outside saved range; motion refused")
        return current

    def gripper_position(self):
        return self.bus.read("Present_Position", "gripper", normalize=False, num_retry=0)

    def validate_gripper(self):
        return self._validate_joints(("gripper",))["gripper"]

    def move_gripper(self, value):
        if not math.isfinite(value):
            raise ValueError("Non-finite gripper target")
        low, high = self.limits("gripper")
        self.gripper_commanded = True
        self.bus.write("Goal_Position", "gripper", round(min(high, max(low, value))),
                       normalize=False, num_retry=0)

    def latch_gripper_and_enable(self, position):
        self.move_gripper(position)
        self.bus.write("Torque_Enable", "gripper", 1, normalize=False, num_retry=0)

    def hold_gripper(self):
        if self.gripper_commanded and self.bus.is_connected:
            self.move_gripper(self.gripper_position())

    def limits(self, name):
        c = self.calibration[name]
        return c.range_min, c.range_max

    def move_raw(self, targets):
        if set(targets) != set(BODY):
            raise ValueError("Need targets for all five arm joints")
        bounded = {}
        for name, value in targets.items():
            if not math.isfinite(value):
                raise ValueError("Non-finite joint target")
            low, high = self.limits(name)
            bounded[name] = round(min(high, max(low, value)))
        self.commanded = True
        self.bus.sync_write("Goal_Position", bounded, normalize=False, num_retry=0)

    def latch_and_enable(self, positions):
        # Some STS3215 firmware enables torque on Goal_Position writes. Seed with
        # actual positions first so enabling never replays an old target.
        self.move_raw(positions)
        self.bus.sync_write("Torque_Enable", {name: 1 for name in BODY}, normalize=False, num_retry=0)

    def hold(self):
        try:
            self.hold_gripper()
        finally:
            if self.commanded and self.bus.is_connected:
                self.move_raw(self.positions())

    def hold_enabled(self):
        """Hold only already-powered joints; never re-enable a released servo.

        Used by the wearable physical controller. An exact measured hold also
        avoids commanding a correction back into saved limits during a stop.
        """
        names = list(BODY) if self.commanded else []
        if self.gripper_commanded: names.append("gripper")
        if not names or not self.bus.is_connected: return
        torque = self.bus.sync_read("Torque_Enable",names,normalize=False,num_retry=0)
        enabled = [name for name in names if torque[name]==1]
        if not enabled: return
        positions = self.bus.sync_read("Present_Position",enabled,normalize=False,num_retry=0)
        if any(not math.isfinite(value) or not 0<=value<=4095 for value in positions.values()):
            raise ValueError("Cannot hold: invalid measured servo position")
        self.bus.sync_write("Goal_Position",{n:round(positions[n]) for n in enabled},normalize=False,num_retry=0)
