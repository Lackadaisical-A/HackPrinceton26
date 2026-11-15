"""Calibration invariants and command watchdog behavior, without physical motors."""
import math
import unittest

from so101_sim.mapper import multiply
from wearable_test.model import ArmModel, ROLES, AXES, stable_average, rotate, dot, mounting_basis
from wearable_test.io import Board, DemoHub, fresh_frame, axis_q, driver_status


class CalibrationTests(unittest.TestCase):
    def assertVector(self, a, b):
        for x, y in zip(a, b): self.assertAlmostEqual(x, y, places=6)

    def test_arbitrary_mounts_and_independent_world_headings(self):
        # Each sensor has a different 3D mounting and independent game-RV yaw.
        mounts = {s: multiply(axis_q(0,.37+i), multiply(axis_q(1,-.6+i*.3),axis_q(2,.42+i))) for i,s in enumerate(ROLES.values())}
        headings = {s: axis_q(2,-1+i*.8) for i,s in enumerate(ROLES.values())}
        def observed(rotations):
            return {s: multiply(multiply(headings[s], rotations[s]), mounts[s]) for s in mounts}
        neutral = observed(dict.fromkeys(mounts, (1,0,0,0)))
        lowered = observed(dict.fromkeys(mounts, axis_q(1, math.pi/2)))
        model = ArmModel()
        model.calibrate(neutral, lowered)
        geometry = model.geometry(neutral)
        self.assertVector(geometry["joints"][-1], (.74,0,0))
        geometry = model.geometry(lowered)
        self.assertVector(geometry["joints"][-1], (0,0,-.74))
        rotations = {s: multiply(axis_q(2,.2*i),axis_q(1,-.4-.3*i)) for i,s in enumerate(mounts)}
        geometry = model.geometry(observed(rotations))
        for role, sensor in ROLES.items():
            for i, axis in enumerate(((1,0,0),(0,1,0),(0,0,1))):
                self.assertVector(geometry["bases"][role][i], rotate(rotations[sensor], axis))

    def test_sensor_distances_are_from_wrist_and_elbow(self):
        geometry = ArmModel().geometry()
        self.assertVector(geometry["markers"]["upper"], (.225,0,0))
        self.assertVector(geometry["markers"]["forearm"], (.3+.26*2/3,0,0))
        self.assertVector(geometry["markers"]["hand"], (.65,0,0))

    def test_manual_axis_and_bad_calibration(self):
        basis = mounting_basis((1,0,0,0), forward_axis="+X")
        self.assertEqual(basis, ((1,0,0),(0,1,0),(0,0,1)))
        with self.assertRaises(ValueError): mounting_basis((1,0,0,0), forward_axis="+Z")
        with self.assertRaises(ValueError): mounting_basis((1,0,0,0), axis_q(1,.1))
        with self.assertRaises(ValueError): mounting_basis((1,0,0,0), axis_q(2,math.pi/2))
        with self.assertRaises(ValueError): ArmModel({"upper_cm": float("nan")})

    def test_average_handles_sign_and_rejects_motion_or_few_samples(self):
        average = stable_average([(1,0,0,0),(-1,0,0,0)]*15)
        self.assertEqual(abs(average[0]), 1)
        with self.assertRaises(ValueError): stable_average([(1,0,0,0)]*19)
        with self.assertRaises(ValueError): stable_average([(1,0,0,0)]*20+[axis_q(0,.5)])


class ProtocolTests(unittest.TestCase):
    def ready(self, name="wroom", driver=1):
        board = Board(name, "COM_TEST")
        board.connected = True
        board.status_requested_at = 10
        device = "WROOM_IO_V1" if name == "wroom" else "S3_IO_V1"
        board.ingest(f"# DEVICE {device} DRV2605L={driver} PWM2=1 JOYSTICK=1",10)
        board.next_status = 13
        return board

    def test_acknowledged_continuous_refresh_and_independent_stop(self):
        board = self.ready()
        board.start(1,50,1,10)
        board.start(2,100,2,10)
        commands = board.commands(10)
        self.assertIn("VIBRATE,500,64",commands)
        self.assertIn("VIBRATE2,500,255",commands)
        # No resend until each board acknowledges; samples still parse during vibration.
        self.assertEqual(board.commands(10.11), [])
        board.ingest("# HAPTIC ON 500",10.12)
        board.ingest("# HAPTIC2 ON 500 255",10.12)
        board.ingest("1,0,0,0",10.12)
        self.assertEqual(board.samples["wroom"]["sequence"],1)
        self.assertIn("VIBRATE,500,64", board.commands(10.13))
        board.stop(1)
        self.assertEqual(board.commands(10.14), ["STOP1"])
        self.assertEqual(board.motors[2]["percent"],100)

    def test_ack_timeout_stops_both_and_never_restarts(self):
        board = self.ready()
        board.start(1,40,10,10)
        board.start(2,40,10,10)
        board.commands(10)
        self.assertEqual(board.commands(10.31),["STOP1","STOP2"])
        self.assertIn("acknowledgment timed out",board.message)
        board.ingest("# HAPTIC ON 500",10.32)
        self.assertEqual(board.commands(10.4), [])

    def test_missing_driver_stale_status_wrong_board_and_run_limit(self):
        board = self.ready(driver=0)
        with self.assertRaises(ValueError): board.start(1,40,1,10)
        board.start(2,40,1,10)
        with self.assertRaises(ValueError): board.start(2,40,11,10)
        with self.assertRaises(ValueError): board.start(2,40,1,17)
        board.ingest("# DEVICE S3_IO_V1 DRV2605L=1 PWM2=1",10.1)
        self.assertFalse(board.available(2,10.2))
        self.assertEqual(board.motors[2]["percent"],0)

    def test_driver_diagnostics_distinguish_cached_failure_from_no_ack(self):
        board = self.ready(driver=0)
        self.assertIn("cannot distinguish", driver_status(board.snapshot(),10))
        board.ingest("# DEVICE WROOM_IO_V1 DRV2605L=0 DRV_INIT=0 DRV_ACK=1 DRV_ERR=0 DRV_REASON=CONFIG_VERIFY PWM2=1 HAPTIC_FW=2",10.1)
        self.assertIn("I2C responds; init not ready",driver_status(board.snapshot(),10.1))
        board.ingest("# DEVICE WROOM_IO_V1 DRV2605L=0 DRV_INIT=1 DRV_ACK=0 DRV_ERR=2 DRV_REASON=READY PWM2=1 HAPTIC_FW=2",10.2)
        self.assertIn("no I2C response",driver_status(board.snapshot(),10.2))
        self.assertIn("waiting",driver_status(board.snapshot(),17))

    def test_driver_reappears_without_automatically_starting_a_motor(self):
        board = self.ready(driver=0)
        board.commands(10)
        board.ingest("# DEVICE WROOM_IO_V1 DRV2605L=1 DRV_INIT=1 DRV_ACK=1 DRV_ERR=0 DRV_REASON=READY PWM2=1 HAPTIC_FW=2",10.1)
        self.assertTrue(board.available(1,10.1))
        self.assertEqual(driver_status(board.snapshot(),10.1),"ready at 0x5A")
        self.assertFalse(any(command.startswith("VIBRATE") for command in board.commands(10.2)))

    def test_expiry_error_reset_and_recovery_stop_motor(self):
        for line in ("# ERROR PWM2 write failed", "# IMU RESET imu1", "# IMU RECOVERY imu2", "# HAPTIC2 OFF"):
            board = self.ready("s3")
            board.start(2,50,2,10)
            board.commands(10)
            board.ingest(line,10.1)
            self.assertEqual(board.motors[2]["percent"],0, line)
            self.assertIn("STOP2",board.commands(10.2))

    def test_joystick_does_not_refresh_imu_and_bad_samples_ignored(self):
        board = self.ready()
        board.ingest("1,0,0,0",10)
        board.ingest("# JOYSTICK,100,1234,2345,1",11)
        self.assertEqual(board.samples["wroom"]["seen"],10)
        self.assertEqual(board.joystick["x"],1234)
        for line in ("# JOYSTICK,105,4096,2000,0", "nan,0,0,0", "0,0,0,0", "1,0,0", "# other,1,2,3,4"):
            board.ingest(line,11.1)
        self.assertEqual(board.samples["wroom"]["sequence"],1)
        self.assertEqual(board.joystick["x"],1234)
        board.ingest("# JOYSTICK,2,2000,2000,0",11.2)
        self.assertEqual(board.epoch,1)
        self.assertIsNone(board.samples["wroom"]["q"])

    def test_three_streams_freshness_and_s3_tags(self):
        wroom, s3 = self.ready(), self.ready("s3")
        wroom.ingest("1,0,0,0",10)
        s3.ingest("imu1,1,0,0,0",10.02)
        s3.ingest("imu2,1,0,0,0",10.03)
        snapshot = {"wroom":wroom.snapshot(), "s3":s3.snapshot()}
        frame, _ = fresh_frame(snapshot,10.1)
        self.assertEqual(set(frame),set(ROLES.values()))
        with self.assertRaises(ValueError): fresh_frame(snapshot,10.5)
        s3.ingest("imu1,1,0,0,0",11.5)
        self.assertEqual(s3.epoch,0)  # Timing gaps alone don't prove the sensor restarted.
        self.assertIn("STOP2",s3.commands(11.5))

    def test_delayed_status_preserves_calibration_but_startup_clears_it(self):
        for name,marker,sample in (("s3","# S3_DUAL_IMU_V3 two motor outputs","imu1,1,0,0,0"),
                                   ("wroom","# w,x,y,z","1,0,0,0")):
            board = self.ready(name)
            board.ingest(sample,10)
            board.ingest(f"# DEVICE {'S3_IO_V1' if name == 's3' else 'WROOM_IO_V1'} DRV2605L=1 PWM2=1",12)
            self.assertEqual(board.epoch,0)
            board.start(2,50,2,12)
            board.ingest(marker,12.1)
            self.assertEqual(board.epoch,1)
            self.assertIn("startup",board.snapshot()["reset_reason"])
            board.note("Status is healthy again")
            self.assertIn("startup",board.snapshot()["reset_reason"])
            self.assertTrue(all(s["q"] is None for s in board.samples.values()))
            self.assertEqual(board.motors[2]["percent"],0)

    def test_zero_and_duration_expiry(self):
        board = self.ready()
        board.start(2,50,1,10)
        board.commands(10)
        board.ingest("# HAPTIC2 ON 500 128",10.01)
        self.assertEqual(board.commands(11.01),["STOP2"])
        board.start(2,50,1,11.1)
        board.set_intensity(2,0)
        self.assertEqual(board.commands(11.2),["STOP2"])


if __name__ == "__main__": unittest.main()
