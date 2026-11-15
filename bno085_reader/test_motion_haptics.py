import math
import unittest
from unittest.mock import patch

from motion_haptics import (
    MotionTrigger, NeutralFeedback, angle_degrees, capture_neutral,
    handshake, quaternion, run, run_neutral,
)


def pose(degrees):
    angle = math.radians(degrees) / 2
    return math.cos(angle), 0, 0, math.sin(angle)


class MotionTests(unittest.TestCase):
    def test_neutral_offset_stays_on_while_held_and_scales_with_angle(self):
        feedback = NeutralFeedback(pose(30))
        self.assertEqual(feedback.update(pose(30))[0], 0)
        self.assertEqual(feedback.update(pose(34))[0], 0)
        low, _ = feedback.update(pose(40))
        high, _ = feedback.update(pose(60))
        self.assertGreater(low, 0)
        self.assertGreater(high, low)
        for _ in range(100):
            self.assertEqual(feedback.update(pose(60))[0], high)
        self.assertEqual(feedback.update(pose(100))[0], 64)
        self.assertEqual(feedback.update(pose(30))[0], 0)
        self.assertEqual(feedback.update(tuple(-v for v in pose(30)))[0], 0)

    def test_neutral_capture_handles_sign_changes(self):
        clock = [0.0]
        class Samples:
            def readline(self):
                clock[0] += .02
                sign = 1 if round(clock[0]*50) % 2 else -1
                return (",".join(str(sign*v) for v in pose(25)) + "\n").encode()
        with patch("motion_haptics.time.monotonic", side_effect=lambda: clock[0]), patch("builtins.print"):
            neutral = capture_neutral(Samples())
        self.assertAlmostEqual(angle_degrees(neutral, pose(25)), 0, places=5)

    def test_continuous_refresh_return_to_neutral_and_stale_stop(self):
        clock = [0.0]
        class Device:
            def __init__(self):
                self.commands, self.responses, self.expirations = [], [], []
                self.expires = None
            def write(self, data):
                self.commands.append((clock[0], data))
                if data == b"STATUS\n":
                    self.responses.append(b"# DEVICE WROOM_IO_V1 IMU=1 DRV2605L=1 MOTOR=ERM\n")
                elif data.startswith(b"VIBRATE"):
                    self.expires = clock[0] + .3
                    self.responses.append(b"# HAPTIC ON 300\n")
                elif data == b"STOP\n":
                    self.expires = None
            def flush(self): pass
            def readline(self):
                clock[0] += .02
                if self.expires is not None and clock[0] >= self.expires:
                    self.expirations.append(clock[0])
                    self.expires = None
                if self.responses: return self.responses.pop(0)
                if clock[0] >= 2.5: return b""
                q = pose(60 if .5 <= clock[0] < 2 else 30)
                return (",".join(map(str, q)) + "\n").encode()
        device = Device()
        with patch("motion_haptics.time.monotonic", side_effect=lambda: clock[0]), patch("motion_haptics.capture_neutral", return_value=pose(30)), patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "IMU stream stopped"):
                run_neutral(device)
        drives = [(t, data) for t, data in device.commands if data.startswith(b"VIBRATE")]
        self.assertGreater(len(drives), 10)
        self.assertTrue(all(.5 <= t < 2 for t, _ in drives))
        self.assertEqual(device.expirations, [])
        self.assertTrue(any(2 <= t < 2.2 and data == b"STOP\n" for t, data in device.commands))
        self.assertEqual(device.commands[-1][1], b"STOP\n")

    def test_quaternion_sign_and_invalid_samples(self):
        q = pose(30)
        self.assertAlmostEqual(angle_degrees(q, tuple(-v for v in q)), 0)
        self.assertIsNone(quaternion("nan,0,0,1"))
        self.assertIsNone(quaternion("0,0,0,0"))
        self.assertIsNone(quaternion("# HAPTIC ON 200"))
        self.assertEqual(quaternion("2,0,0,0"), (1, 0, 0, 0))

    def test_motion_requires_stillness_before_retrigger(self):
        detector = MotionTrigger()
        events = []
        # Stationary, then continuous rotation: exactly one pulse.
        for i in range(151):
            t = i * .02
            rotation = max(0, t - .5) * 100
            if detector.update(t, pose(rotation))[0]:
                events.append(t)
        self.assertEqual(len(events), 1)
        # Hold, re-arm, and rotate again.
        for i in range(151, 201):
            self.assertFalse(detector.update(i * .02, pose(250))[0])
        self.assertTrue(detector.armed)
        for i in range(201, 226):
            if detector.update(i * .02, pose(250 + (i-200)*2))[0]:
                events.append(i * .02)
        self.assertEqual(len(events), 2)

    def test_missing_driver_fails_before_motor_command(self):
        class MissingDriver:
            commands = []
            def write(self, data): self.commands.append(data)
            def readline(self): return b"# DEVICE WROOM_IO_V1 IMU=1 DRV2605L=0 MOTOR=ERM\n"
        connection = MissingDriver()
        with self.assertRaisesRegex(RuntimeError, "Both BNO085"):
            handshake(connection)
        self.assertFalse(any(c.startswith(b"VIBRATE") for c in connection.commands))

    def test_stream_and_commands_share_connection_and_stop_on_disconnect(self):
        clock = [0.0]

        class Device:
            def __init__(self):
                self.commands, self.responses = [], []
                self.off = None
            def write(self, data):
                self.commands.append(data)
                if data == b"STATUS\n":
                    self.responses.append(b"# DEVICE WROOM_IO_V1 IMU=1 DRV2605L=1 MOTOR=ERM\n")
                elif data.startswith(b"VIBRATE"):
                    self.responses.append(b"# HAPTIC ON 200\n")
                    self.off = clock[0] + .2
            def flush(self): pass
            def readline(self):
                clock[0] += .02
                if self.responses: return self.responses.pop(0)
                if self.off is not None and clock[0] >= self.off:
                    self.off = None
                    return b"# HAPTIC OFF\n"
                if clock[0] > 2: return b""  # Stream disconnects.
                q = pose(max(0, clock[0] - .5) * 100)
                return (",".join(map(str, q)) + "\n").encode()

        device = Device()
        with patch("motion_haptics.time.monotonic", side_effect=lambda: clock[0]), patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "IMU stream stopped"):
                run(device, 8, 200, 64, 1)
        self.assertEqual(sum(c.startswith(b"VIBRATE") for c in device.commands), 1)
        self.assertEqual(device.commands[-1], b"STOP\n")


if __name__ == "__main__":
    unittest.main()
