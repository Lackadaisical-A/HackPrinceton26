# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Pulse a driver and verify IMU data continues during the requested pulse."""
import argparse
import math
import time

import serial


def run(connection, expected):
    pulse_counts = {}
    pulse_active = False
    def read_for(seconds, counts, messages):
        nonlocal pulse_active
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = connection.readline().decode("ascii", errors="ignore").strip()
            if line.startswith("#"):
                messages.append(line)
                print(line)
                if line.startswith("# HAPTIC ON"):
                    pulse_active = True
                elif line == "# HAPTIC OFF":
                    pulse_active = False
                continue  # Joystick/status packets are not quaternion samples.
            parts = line.split(",")
            label = "imu" if len(parts) == 4 else parts[0]
            values = parts if len(parts) == 4 else parts[1:]
            try:
                if len(values) == 4 and all(math.isfinite(float(v)) for v in values):
                    counts[label] = counts.get(label, 0) + 1
                    if pulse_active:
                        pulse_counts[label] = pulse_counts.get(label, 0) + 1
            except ValueError:
                pass

    try:
        before, messages = {}, []
        print("Waiting for startup and baseline readings...")
        read_for(5, before, messages)
        if len(before) < expected:
            raise RuntimeError(f"Expected {expected} IMUs; baseline counts: {before}. No pulse sent.")
        connection.write(b"PING\n")
        messages = []
        read_for(.5, {}, messages)
        if "# PONG" not in messages:
            raise RuntimeError("No PONG. Upload the haptic smoke sketch first.")
        connection.reset_input_buffer()
        connection.write(b"VIBRATE,500\n")
        during, messages = {}, []
        read_for(1.5, during, messages)
        if "# HAPTIC ON 500" not in messages or "# HAPTIC OFF" not in messages:
            raise RuntimeError("Missing pulse ON/OFF acknowledgment")
        print(f"Baseline: {before}; during motor ON: {pulse_counts}; pulse + recovery: {during}")
        if not all(pulse_counts.get(label, 0) > 0 for label in before):
            raise RuntimeError("An IMU did not report between pulse ON and OFF")
        print("PASS: pulse acknowledged and all IMUs reported while motor output was ON.")
        print("Confirm physically: coin vibrated briefly, stopped, and board did not reset.")
    finally:
        connection.write(b"STOP\n")
        connection.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expected", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    try:
        with serial.Serial(args.port, 115200, timeout=.03) as connection:
            run(connection, args.expected)
    except (serial.SerialException, RuntimeError) as error:
        parser.exit(1, f"FAIL: {error}\n")
