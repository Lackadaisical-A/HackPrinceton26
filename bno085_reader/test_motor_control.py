import unittest
from unittest.mock import patch

from motor_control import is_sample, run


class FakeMotorBoard:
    def __init__(self, clock, *, pwm=True, acknowledge=True, reset_at=None,
                 identity="WROOM_IO_V1", drv=True, reset_message=b"# IMU RESET\n"):
        self.clock = clock
        self.pwm = pwm
        self.acknowledge = acknowledge
        self.reset_at = reset_at
        self.identity = identity
        self.drv = drv
        self.reset_message = reset_message
        self.sample_number = 0
        self.commands = []
        self.responses = []
        self.expires = None
        self.expirations = 0

    def write(self, data):
        self.commands.append((self.clock[0], data))
        if data == b"STATUS\n":
            imus = "IMU1=1 IMU2=1" if self.identity == "S3_IO_V1" else "IMU=1"
            self.responses.append(
                f"# DEVICE {self.identity} {imus} DRV2605L={int(self.drv)} MOTOR=ERM PWM2={int(self.pwm)} PWM_PIN=15\n".encode()
            )
        elif data.startswith((b"VIBRATE,", b"VIBRATE2,")):
            name, duration, strength = data.decode().strip().split(",")
            self.expires = self.clock[0] + int(duration) / 1000
            if self.acknowledge:
                response = f"# HAPTIC2 ON {duration} {strength}" if name == "VIBRATE2" else f"# HAPTIC ON {duration}"
                self.responses.append((response + "\n").encode())
        elif data == b"STOP\n":
            self.expires = None

    def flush(self):
        pass

    def readline(self):
        self.clock[0] += .02
        if self.expires is not None and self.clock[0] >= self.expires:
            self.expirations += 1
            self.expires = None
        if self.reset_at is not None and self.clock[0] >= self.reset_at:
            self.reset_at = None
            return self.reset_message
        if self.responses:
            return self.responses.pop(0)
        self.sample_number += 1
        prefix = f"imu{1 + self.sample_number % 2}," if self.identity == "S3_IO_V1" else ""
        return (prefix + "1,0,0,0\n").encode()


class MotorControlTests(unittest.TestCase):
    def drive(self, board, **kwargs):
        with patch("motor_control.time.monotonic", side_effect=lambda: board.clock[0]), patch("builtins.print"):
            return run(board, **kwargs)

    def test_pwm_is_continuous_and_imu_is_read(self):
        board = FakeMotorBoard([0.0])
        acks, samples = self.drive(board, motor=2, strength=128, seconds=3)
        self.assertGreater(acks, 20)
        self.assertGreater(samples, 50)
        self.assertEqual(board.expirations, 0)
        drives = [data for _, data in board.commands if data.startswith(b"VIBRATE")]
        self.assertTrue(all(data == b"VIBRATE2,500,128\n" for data in drives))
        self.assertEqual(board.commands[-1][1], b"STOP\n")
        self.assertIsNone(board.expires)

    def test_original_motor_uses_existing_protocol(self):
        board = FakeMotorBoard([0.0])
        self.drive(board, motor=1, strength=64, seconds=.5)
        self.assertIn(b"VIBRATE,500,64\n", [data for _, data in board.commands])
        self.assertFalse(any(data.startswith(b"VIBRATE2") for _, data in board.commands))

    def test_missing_pwm_firmware_never_energizes(self):
        board = FakeMotorBoard([0.0], pwm=False)
        with self.assertRaisesRegex(RuntimeError, "PWM2 unavailable"):
            self.drive(board)
        self.assertFalse(any(data.startswith(b"VIBRATE") for _, data in board.commands))
        self.assertEqual(board.commands[-1][1], b"STOP\n")

    def test_missing_ack_stops_before_command_expires(self):
        board = FakeMotorBoard([0.0], acknowledge=False)
        with self.assertRaisesRegex(RuntimeError, "acknowledgment timed out"):
            self.drive(board)
        self.assertLess(board.clock[0], .5)
        self.assertEqual(board.commands[-1][1], b"STOP\n")

    def test_reset_stops_and_does_not_restart_motor(self):
        board = FakeMotorBoard([0.0], reset_at=.3)
        with self.assertRaisesRegex(RuntimeError, "IMU RESET"):
            self.drive(board)
        self.assertEqual(board.commands[-1][1], b"STOP\n")
        self.assertIsNone(board.expires)

    def test_zero_strength_only_stops(self):
        board = FakeMotorBoard([0.0])
        self.assertEqual(self.drive(board, strength=0), (0, 0))
        self.assertFalse(any(data.startswith(b"VIBRATE") for _, data in board.commands))

    def test_invalid_parameters_never_write(self):
        for args in ({"strength": 256}, {"strength": -1}, {"motor": 1, "strength": 128},
                     {"seconds": float("nan")}, {"seconds": 0}):
            board = FakeMotorBoard([0.0])
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.drive(board, **args)
            self.assertEqual(board.commands, [])

    def test_s3_supports_both_motors_and_tagged_samples(self):
        for motor, strength in ((1, 64), (2, 128)):
            with self.subTest(motor=motor):
                board = FakeMotorBoard([0.0], identity="S3_IO_V1")
                acks, samples = self.drive(board, motor=motor, strength=strength, seconds=3)
                self.assertGreater(acks, 20)
                self.assertGreater(samples, 50)
                self.assertEqual(board.expirations, 0)
                self.assertEqual(board.commands[-1][1], b"STOP\n")

    def test_s3_pwm_does_not_require_drv_but_motor1_does(self):
        board = FakeMotorBoard([0.0], identity="S3_IO_V1", drv=False)
        acks, _ = self.drive(board, motor=2, seconds=.5)
        self.assertGreater(acks, 0)
        board = FakeMotorBoard([0.0], identity="S3_IO_V1", drv=False)
        with self.assertRaisesRegex(RuntimeError, "bno085_reader_s3.ino"):
            self.drive(board, motor=1, strength=64)
        self.assertFalse(any(data.startswith(b"VIBRATE") for _, data in board.commands))

    def test_s3_reset_or_recovery_aborts_motor_run(self):
        for message in (b"# IMU RESET imu1; motors stopped\n", b"# IMU RECOVERY imu2; stopping motors\n"):
            with self.subTest(message=message):
                board = FakeMotorBoard([0.0], identity="S3_IO_V1", reset_at=.3, reset_message=message)
                with self.assertRaisesRegex(RuntimeError, "# IMU"):
                    self.drive(board)
                self.assertEqual(board.commands[-1][1], b"STOP\n")
                self.assertIsNone(board.expires)

    def test_sample_formats_require_finite_nonzero_quaternions(self):
        for line in ("1,0,0,0", "imu1,1,0,0,0", "imu2,1,0,0,0"):
            self.assertTrue(is_sample(line))
        for line in ("# IMU RESET", "imu1,nan,0,0,0", "imu2,0,0,0,0", "imu3,1,0,0,0"):
            self.assertFalse(is_sample(line))


if __name__ == "__main__":
    unittest.main()
