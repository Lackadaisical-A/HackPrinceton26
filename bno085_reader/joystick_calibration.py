"""Shared ADC centering and absolute claw-position mapping; no hardware access."""
from dataclasses import dataclass
import json
import math
from pathlib import Path

PROFILE = Path(__file__).with_suffix(".json")


@dataclass(frozen=True)
class JoystickCalibration:
    center_x: float = 1360
    center_y: float = 1810
    deadzone: float = .15

    def __post_init__(self):
        if any(not math.isfinite(v) or not 0 < v < 4095 for v in (self.center_x, self.center_y)):
            raise ValueError("Joystick centers must be inside 0..4095")
        if not math.isfinite(self.deadzone) or not 0 <= self.deadzone < 1:
            raise ValueError("Joystick dead zone must be in [0, 1)")

    def center(self, axis):
        if axis not in ("x", "y"):
            raise ValueError("Joystick axis must be x or y")
        return self.center_x if axis == "x" else self.center_y

    def axis(self, raw, axis):
        if not math.isfinite(raw) or not 0 <= raw <= 4095:
            raise ValueError("Invalid joystick ADC reading")
        center = self.center(axis)
        offset = raw-center
        value = offset/(4095-center if offset >= 0 else center)
        if abs(value) <= self.deadzone:
            return 0.
        return math.copysign((abs(value)-self.deadzone)/(1-self.deadzone), value)

    def normalized(self, x, y):
        return {"x": self.axis(x,"x"), "y": self.axis(y,"y")}

    def position(self, raw, low, high, axis="y", reverse=False):
        if not all(math.isfinite(v) for v in (low,high)) or high <= low:
            raise ValueError("Invalid claw position limits")
        # 0 ADC -> maximum/open; 4095 -> minimum/closed; calibrated center -> half.
        fraction = (1-self.axis(raw,axis))/2
        if reverse:
            fraction = 1-fraction
        return low+(high-low)*fraction


def load_calibration():
    return JoystickCalibration(**json.loads(PROFILE.read_text(encoding="utf-8")))
