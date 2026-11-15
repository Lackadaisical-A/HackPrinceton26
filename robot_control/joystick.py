"""Bounded position control for the claw; raw acquisition stays on the WROOM."""
import math
from bno085_reader.joystick_calibration import load_calibration
from robot_control.grasp import GraspHold


class JoystickClaw:
    def __init__(self, axis="y", reverse=False, calibration=None, speed=.2, max_lead=.02):
        if axis not in ("x", "y"):
            raise ValueError("Joystick axis must be x or y")
        self.axis, self.reverse = axis, reverse
        self.calibration = calibration or load_calibration()
        if (any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (speed,max_lead))
                or not 0<speed<=3 or not 0<max_lead<=.3):
            raise ValueError('Invalid claw speed or lead')
        self.speed = speed  # Fraction of calibrated travel per second.
        self.max_lead = max_lead  # Fraction of travel ahead of measured position.
        self.load_limit = 350  # Raw servo-load counts, not force units.
        self.load_stops_enabled = True
        self.enabled = False
        self.grasp = GraspHold()
        self.status = "Claw OFF: release joystick, then enable position control (neutral = half open)"

    def fresh(self, sample, now):
        if not sample["sequence"] or not 0 <= now-sample["seen"] <= .3:
            raise ValueError("joystick disconnected/stale")
        if sample["pressed"]:
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
        self.grasp.reset()
        self.enabled = True
        endpoints = "0=closed / 4095=open" if self.reverse else "0=open / 4095=closed"
        self.status = f"Claw POSITION: {self.axis.upper()} {endpoints}; neutral=half open; button pauses"

    def pause(self, reason="paused"):
        self.enabled = False
        self.grasp.reset()
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
        if self.load_stops_enabled and abs(load) >= self.load_limit:
            raise ValueError(f"high gripper load: {abs(load):g} >= {self.load_limit:g} raw counts")
        desired = self.calibration.position(sample[self.axis], self.low, self.high, self.axis, self.reverse)
        return self.position_target(desired,measured,load,dt,now)

    def position_target(self,desired,measured,load,dt,now):
        if not all(math.isfinite(v) for v in (desired,measured,load,dt)) or dt<0:
            raise ValueError('Invalid gripper telemetry')
        if not self.low<=measured<=self.high: raise ValueError('Gripper outside calibrated range')
        if self.load_stops_enabled and abs(load)>=self.load_limit:
            raise ValueError(f'high gripper load: {abs(load):g} >= {self.load_limit:g} raw counts')
        desired = max(self.low,min(self.high,desired))
        travel = self.high-self.low
        step = self.speed*travel*min(.1, max(0., dt))
        if self.grasp.request is not None and (desired>self.grasp.request+8 or desired>measured+8):
            self.previous = measured
        target = self.previous + max(-step, min(step, desired-self.previous))
        target = min(measured+self.max_lead*travel, max(measured-self.max_lead*travel, target))
        target = self.grasp.update(desired,target,self.previous,measured,load,(self.low,self.high),min(.1,dt),now)
        self.previous = min(self.high, max(self.low, target))
        return self.previous
