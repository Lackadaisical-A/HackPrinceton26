"""Bounded position control for the claw; raw acquisition stays on the WROOM."""
import math
from bno085_reader.joystick_calibration import load_calibration


class JoystickClaw:
    def __init__(self, axis="y", reverse=False, calibration=None, button_stops=True):
        if axis not in ("x", "y"):
            raise ValueError("Joystick axis must be x or y")
        self.axis, self.reverse = axis, reverse
        # False when the caller reads the button itself (wearable mode: tap records, hold stops).
        self.button_stops = button_stops
        self.calibration = calibration or load_calibration()
        self.speed = .2  # Fraction of calibrated travel per second.
        self.max_lead = .02  # Fraction of travel ahead of measured position.
        self.load_limit = 350  # Raw servo-load counts, not force units.
        self.enabled = False
        self.status = "Claw OFF: release joystick, then enable position control (neutral = half open)"

    def fresh(self, sample, now):
        if not sample["sequence"] or not 0 <= now-sample["seen"] <= .3:
            raise ValueError("joystick disconnected/stale")
        if sample["pressed"] and self.button_stops:
            raise ValueError("joystick button pressed")
        if any(not 0 <= sample[axis] <= 4095 for axis in ("x", "y")):
            raise ValueError("invalid joystick readings")

    def enable(self, sample, measured, limits, now):
        self.enabled = False
        self.fresh(sample, now)
        if any(self.calibration.normalized(sample["x"],sample["y"]).values()):
            raise ValueError("release joystick to center before enabling")
        low, high = limits
        if not low <= measured <= high or high <= low:
            raise ValueError("gripper position outside saved range")
        self.center, self.epoch = self.calibration.center(self.axis), sample["epoch"]
        self.low, self.high = low, high
        self.previous = measured
        self.enabled = True
        endpoints = "0=closed / 4095=open" if self.reverse else "0=open / 4095=closed"
        self.status = f"Claw POSITION: {self.axis.upper()} {endpoints}; neutral=half open; button pauses"

    def pause(self, reason="paused"):
        self.enabled = False
        self.status = f"Claw PAUSED: {reason}; enable claw position control to resume"

    def step(self, sample, measured, load, dt, now):
        if not self.enabled:
            raise ValueError("claw control is not enabled")
        self.fresh(sample, now)
        if sample["epoch"] != self.epoch:
            raise ValueError("WROOM restarted/reconnected")
        if not all(math.isfinite(v) for v in (measured, load, dt)):
            raise ValueError("invalid gripper telemetry")
        if not self.low <= measured <= self.high:
            raise ValueError("gripper position outside saved range")
        if abs(load) >= self.load_limit:
            raise ValueError("high gripper load")
        # Pushing the stick down wobbles its axes: keep the claw where it is meanwhile.
        desired = self.previous if sample["pressed"] else \
            self.calibration.position(sample[self.axis], self.low, self.high, self.axis, self.reverse)
        travel = self.high-self.low
        step = self.speed*travel*min(.1, max(0., dt))
        target = self.previous + max(-step, min(step, desired-self.previous))
        target = min(measured+self.max_lead*travel, max(measured-self.max_lead*travel, target))
        self.previous = min(self.high, max(self.low, target))
        return self.previous
