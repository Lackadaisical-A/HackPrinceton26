"""Convert servo load magnitude into continuous coin vibration."""
import math


class LoadFeedback:
    def __init__(self, deadband=25, full_scale=95, max_strength=127, reverse=False):
        if not 0 < deadband < full_scale <= 1023 or not 30 <= max_strength <= 127:
            raise ValueError("Require 0 < deadband < full_scale <= 1023 and 30 <= strength <= 127")
        self.deadband, self.full_scale = deadband, full_scale
        self.max_strength = max_strength
        self.sign = -1 if reverse else 1
        self.baseline = 0.0
        self.load = 0.0
        self.seen = None
        self.direction = 0

    def reset(self):
        self.seen = None
        self.load = 0.0
        self.direction = 0

    def zero(self, raw_load):
        if not math.isfinite(raw_load) or abs(raw_load) > 1023:
            raise ValueError("Invalid load")
        self.baseline = raw_load
        self.reset()

    def update(self, raw_load, now):
        if not math.isfinite(raw_load) or abs(raw_load) > 1023:
            self.reset()
            raise ValueError("Invalid load")
        value = self.sign * (raw_load-self.baseline)
        if self.seen is None or now-self.seen > .3:
            self.direction = 0
        # Use the latest relative load: no filter tail or hysteresis may keep
        # the coin running below the cutoff, or delay full intensity above it.
        self.load = value
        self.seen = now
        self.direction = (1 if self.load > 0 else -1) if abs(self.load) >= self.deadband else 0

    def output(self, now):
        if self.seen is None or now-self.seen > .3:
            return 0, "OFF: robot load missing/stale"
        if not self.direction:
            return 0, f"Idle: load {self.load:+.0f}, deadband {self.deadband}"
        amount = min(1, max(0, (abs(self.load)-self.deadband)/(self.full_scale-self.deadband)))
        strength = round(30+(self.max_strength-30)*amount)
        label = "LEFT" if self.direction < 0 else "RIGHT"
        return strength, f"{label}: load {self.load:+.0f}, continuous intensity {strength}/{self.max_strength}"
