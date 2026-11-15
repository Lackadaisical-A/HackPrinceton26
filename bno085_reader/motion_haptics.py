# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Host-side orientation feedback: WROOM IMU -> Python -> DRV2605L."""
import argparse
from collections import deque
import math
import time

import serial


def quaternion(line):
    try:
        q = tuple(float(v) for v in line.split(","))
    except ValueError:
        return None
    if len(q) != 4 or not all(math.isfinite(v) for v in q):
        return None
    norm = math.sqrt(sum(v*v for v in q))
    return tuple(v/norm for v in q) if norm > 1e-6 else None


def angle_degrees(a, b):
    # q and -q encode exactly the same physical orientation.
    dot = min(1.0, max(0.0, abs(sum(x*y for x, y in zip(a, b)))))
    return math.degrees(2 * math.acos(dot))


class MotionTrigger:
    def __init__(self, threshold=8.0, window=.2, cooldown=1.0):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self.history = deque()
        self.armed = True
        self.last_trigger = -math.inf
        self.quiet_since = None

    def update(self, now, q):
        self.history.append((now, q))
        while len(self.history) > 1 and self.history[1][0] <= now - self.window:
            self.history.popleft()
        if now - self.history[0][0] < self.window * .8:
            return False, 0.0
        angle = angle_degrees(self.history[0][1], q)
        if not self.armed:
            if now - self.last_trigger >= self.cooldown and angle < self.threshold / 2:
                if self.quiet_since is None:
                    self.quiet_since = now
                elif now - self.quiet_since >= .4:
                    self.armed = True
            else:
                self.quiet_since = None
        if self.armed and angle >= self.threshold:
            self.armed = False
            self.last_trigger = now
            self.quiet_since = None
            return True, angle
        return False, angle


def handshake(connection, timeout=8.0):
    deadline = time.monotonic() + timeout
    next_status = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_status:
            connection.write(b"STATUS\n")
            next_status = now + .5
        line = connection.readline().decode("ascii", errors="ignore").strip()
        if line.startswith("# DEVICE WROOM_IO_V1 "):
            print(line, flush=True)
            if "IMU=1" not in line or "DRV2605L=1" not in line:
                raise RuntimeError("Both BNO085 and DRV2605L must be detected. Check shared SDA21/SCL22 and power.")
            return
    raise RuntimeError("WROOM_IO_V1 firmware not detected. Upload wroom_io/wroom_io.ino once to COM7.")


class NeutralFeedback:
    def __init__(self, neutral, deadzone=5, full_scale=45, strength=64, minimum=30):
        self.neutral = neutral
        self.deadzone, self.full_scale = deadzone, full_scale
        self.strength, self.minimum = strength, minimum
        self.active = False

    def update(self, q):
        angle = angle_degrees(self.neutral, q)
        # One-degree hysteresis keeps noise near neutral from switching the motor.
        if angle <= self.deadzone:
            self.active = False
        elif angle >= self.deadzone + min(1, (self.full_scale-self.deadzone)/2):
            self.active = True
        if not self.active:
            return 0, angle
        fraction = min(1, max(0, (angle-self.deadzone)/(self.full_scale-self.deadzone)))
        return round(self.minimum + fraction*(self.strength-self.minimum)), angle


def capture_neutral(connection):
    print("Hold the IMU still in your neutral pose for one second...", flush=True)
    started = time.monotonic()
    stable_since = None
    samples = []
    while time.monotonic() - started < 15:
        line = connection.readline().decode("ascii", errors="ignore").strip()
        if line.startswith("# ERROR") or line == "# IMU RESET":
            raise RuntimeError(line)
        q = quaternion(line)
        if q is None:
            continue
        now = time.monotonic()
        if not samples or angle_degrees(samples[0], q) > 3:
            samples = [q]
            stable_since = now
            continue
        if sum(a*b for a, b in zip(samples[0], q)) < 0:
            q = tuple(-v for v in q)
        samples.append(q)
        if now - stable_since >= 1 and len(samples) >= 20:
            mean = tuple(sum(values)/len(samples) for values in zip(*samples))
            norm = math.sqrt(sum(v*v for v in mean))
            print("Neutral pose saved. Tilt away to vibrate; return to neutral to stop.", flush=True)
            return tuple(v/norm for v in mean)
    raise RuntimeError("Could not capture a stable neutral pose. Hold still and check IMU readings.")


def run_neutral(connection, deadzone=5, full_scale=45, strength=64, minimum=30):
    try:
        handshake(connection)
        connection.write(b"STOP\n")
        neutral = capture_neutral(connection)
        feedback = NeutralFeedback(neutral, deadzone, full_scale, strength, minimum)
        print(f"Continuous feedback: off within {deadzone:g}deg; maximum strength {strength} at {full_scale:g}deg. Ctrl+C stops.", flush=True)
        last_sample = time.monotonic()
        next_refresh = 0
        next_log = 0
        last_strength = 0
        pending_on = None
        while True:
            line = connection.readline().decode("ascii", errors="ignore").strip()
            now = time.monotonic()
            if line.startswith("# ERROR") or line == "# IMU RESET":
                raise RuntimeError(line + "; restart to capture neutral again.")
            if line.startswith("# HAPTIC ON"):
                pending_on = None
            if pending_on is not None and now > pending_on:
                raise RuntimeError("No vibration acknowledgment received")
            if now - last_sample > .5:
                raise RuntimeError("IMU stream stopped for 0.5 seconds")
            q = quaternion(line)
            if q is None:
                continue
            last_sample = now
            drive, angle = feedback.update(q)
            if drive == 0 and last_strength:
                connection.write(b"STOP\n")
                last_strength = 0
                next_refresh = now
            elif drive > 0 and now >= next_refresh and pending_on is None:
                # Refresh BEFORE the 300ms expiry: continuous output with a
                # firmware timeout if the host exits or stops receiving samples.
                connection.write(f"VIBRATE,300,{drive}\n".encode("ascii"))
                pending_on = now + .5
                last_strength = drive
                next_refresh = now + .1
            if now >= next_log:
                print(f"From neutral: {angle:5.1f}deg | strength {drive:3d}/{strength} | {'VIBRATING' if drive else 'OFF'}", flush=True)
                next_log = now + .5
    finally:
        try:
            connection.write(b"STOP\n")
            connection.flush()
        except serial.SerialException:
            pass


def run(connection, threshold, pulse_ms, strength, cooldown):
    try:
        handshake(connection)
        connection.write(b"STOP\n")
        trigger = MotionTrigger(threshold=threshold, cooldown=cooldown)
        print(f"Ready: rotate/tilt about {threshold:g} degrees within 0.2s -> {pulse_ms}ms pulse (strength {strength}).", flush=True)
        print("Hold still between gestures to re-arm. Ctrl+C stops.", flush=True)
        last_sample = time.monotonic()
        pending_on = None
        pending_off = None
        next_log = 0
        samples = 0
        while True:
            line = connection.readline().decode("ascii", errors="ignore").strip()
            now = time.monotonic()
            if line.startswith("# ERROR") or line == "# IMU RESET":
                raise RuntimeError(line + "; stop and rerun after checking the device.")
            if line.startswith("# HAPTIC ON"):
                print(line, flush=True)
                pending_on = None
                pending_off = now + pulse_ms / 1000 + 1
            elif line == "# HAPTIC OFF":
                print(line, flush=True)
                pending_off = None
            if pending_on is not None and now > pending_on:
                raise RuntimeError("No vibration acknowledgment received")
            if pending_off is not None and now > pending_off:
                raise RuntimeError("No motor OFF acknowledgment received")
            q = quaternion(line)
            if q is not None:
                samples += 1
                last_sample = now
                fire, angle = trigger.update(now, q)
                if fire and pending_on is None and pending_off is None:
                    connection.write(f"VIBRATE,{pulse_ms},{strength}\n".encode("ascii"))
                    pending_on = now + 1
                    print(f"Motion {angle:.1f} degrees -> requesting pulse", flush=True)
                if now >= next_log:
                    print(f"IMU samples={samples} motion={angle:.1f}deg {'armed' if trigger.armed else 'waiting for stillness'}", flush=True)
                    next_log = now + 1
            if now - last_sample > 2:
                raise RuntimeError("IMU stream stopped for 2 seconds")
    finally:
        try:
            connection.write(b"STOP\n")
            connection.flush()
        except serial.SerialException:
            pass  # Firmware also expires each pulse automatically.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--mode", choices=["neutral", "gesture"], default="neutral")
    parser.add_argument("--threshold-deg", type=float, default=5, help="Neutral deadzone (or gesture threshold)")
    parser.add_argument("--full-scale-deg", type=float, default=45)
    parser.add_argument("--min-strength", type=int, default=30)
    parser.add_argument("--pulse-ms", type=int, default=200)
    parser.add_argument("--strength", type=int, default=64)
    parser.add_argument("--cooldown", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 < args.threshold_deg <= 180 or not 1 <= args.pulse_ms <= 500 or not 1 <= args.strength <= 127 or not 1 <= args.cooldown <= 60:
        parser.error("Use threshold 0..180 degrees, pulse 1..500ms, strength 1..127, cooldown 1..60s")
    if args.mode == "neutral" and (not args.threshold_deg < args.full_scale_deg <= 180 or not 1 <= args.min_strength <= args.strength):
        parser.error("Full-scale angle must exceed threshold and be <=180; min-strength must be 1..strength")
    try:
        with serial.Serial(args.port, 115200, timeout=.02, write_timeout=1) as connection:
            if args.mode == "neutral":
                run_neutral(connection, args.threshold_deg, args.full_scale_deg, args.strength, args.min_strength)
            else:
                run(connection, args.threshold_deg, args.pulse_ms, args.strength, args.cooldown)
    except KeyboardInterrupt:
        print("\nStopped; motor STOP requested.")
    except (serial.SerialException, RuntimeError) as error:
        parser.exit(1, f"Stopped: {error}\nClose other programs using this COM port before retrying.\n")


if __name__ == "__main__":
    main()
