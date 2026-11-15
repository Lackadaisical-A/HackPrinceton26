# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Drive either coin motor on WROOM or S3 while reading IMU data."""
import argparse
import math
import time

import serial

DEVICE_PREFIXES = ("# DEVICE WROOM_IO_V1 ", "# DEVICE S3_IO_V1 ")


def read_line(connection):
    line = connection.readline().decode("ascii", errors="ignore").strip()
    if line.startswith(("# ERROR", "# IMU RESET", "# IMU RECOVERY")):
        raise RuntimeError(line)
    return line


def wait_status(connection, motor):
    deadline, next_request = time.monotonic() + 6, 0
    while time.monotonic() < deadline:
        if time.monotonic() >= next_request:
            connection.write(b"STATUS\n")
            next_request = time.monotonic() + .5
        line = read_line(connection)
        if not line.startswith(DEVICE_PREFIXES):
            continue
        fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
        required = "DRV2605L" if motor == 1 else "PWM2"
        if fields.get(required) != "1":
            sketch = "bno085_reader_s3.ino" if line.startswith("# DEVICE S3_IO_V1 ") else "wroom_io.ino"
            raise RuntimeError(f"{required} unavailable. Upload updated {sketch} and check wiring.")
        return line
    raise RuntimeError("No compatible motor firmware response. Check COM port and upload the WROOM or S3 sketch.")


def is_sample(line):
    try:
        parts = line.split(",")
        if len(parts) == 5 and parts[0] in ("imu1", "imu2"):
            parts = parts[1:]
        q = [float(v) for v in parts]
        return len(q) == 4 and all(math.isfinite(v) for v in q) and sum(v*v for v in q) > 1e-12
    except ValueError:
        return False


def run(connection, motor=2, strength=128, seconds=2):
    maximum = 127 if motor == 1 else 255
    if motor not in (1, 2) or not 0 <= strength <= maximum:
        raise ValueError(f"Motor {motor} strength must be 0..{maximum}")
    if not math.isfinite(seconds) or not .1 <= seconds <= 3600:
        raise ValueError("Seconds must be between 0.1 and 3600")
    try:
        # This process owns the selected port; start with both outputs idle.
        connection.write(b"STOP\n")
        print(wait_status(connection, motor))
        if strength == 0:
            return 0, 0
        prefix = "VIBRATE" if motor == 1 else "VIBRATE2"
        acknowledgment = "# HAPTIC ON 500" if motor == 1 else f"# HAPTIC2 ON 500 {strength}"
        drive = f"{prefix},500,{strength}\n".encode()
        deadline = time.monotonic() + seconds
        next_refresh, awaiting = 0, None
        samples, acknowledgments = 0, 0
        while time.monotonic() < deadline or awaiting is not None:
            now = time.monotonic()
            if awaiting is not None and now >= awaiting:
                raise RuntimeError("Motor acknowledgment timed out; stopping.")
            if now < deadline and now >= next_refresh and awaiting is None:
                connection.write(drive)
                awaiting = now + .25
                next_refresh = now + .1
            line = read_line(connection)
            if line == acknowledgment:
                awaiting = None
                acknowledgments += 1
            elif line.startswith("# DEVICE"):
                raise RuntimeError("Board restarted during motor command; stopping.")
            elif line in ("# HAPTIC OFF", "# HAPTIC2 OFF"):
                expected_off = "# HAPTIC OFF" if motor == 1 else "# HAPTIC2 OFF"
                if line == expected_off:
                    raise RuntimeError("Motor command expired unexpectedly; stopping.")
            elif is_sample(line):
                samples += 1
        return acknowledgments, samples
    finally:
        try:
            connection.write(b"STOP\n")
            connection.flush()
        except serial.SerialException:
            # The board's last command expires within 500ms in normal operation.
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--motor", type=int, choices=[1, 2], default=2)
    parser.add_argument("--strength", type=int, default=128)
    parser.add_argument("--seconds", type=float, default=2)
    args = parser.parse_args()
    connection = serial.Serial(port=None, baudrate=115200, timeout=.02, write_timeout=.5)
    connection.dtr = False
    connection.rts = False
    connection.port = args.port
    try:
        connection.open()
        connection.reset_input_buffer()
        with connection:
            acks, samples = run(connection, args.motor, args.strength, args.seconds)
        print(f"Stopped. Motor acknowledgments: {acks}; IMU samples during run: {samples}.")
        print("Confirm physical vibration yourself; acknowledgments only confirm firmware commands.")
    except KeyboardInterrupt:
        print("Stopped.")
    except (serial.SerialException, RuntimeError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\nClose dashboard/robot/Serial Monitor before using {args.port} here.\n")


if __name__ == "__main__":
    main()
