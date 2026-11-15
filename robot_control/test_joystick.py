import time
import unittest
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"bno085_reader"))

from robot_control.arm import Arm
from robot_control.controller import RobotWorker
from robot_control.joystick import JoystickClaw
from dashboard import Hub


def sample(y=1810, x=1360, **kwargs):
    return {"x": x, "y": y, "pressed": False, "sequence": 1, "seen": 1., "epoch": 0, **kwargs}


class JoystickTests(unittest.TestCase):
    def armed(self, measured=2000, **kwargs):
        claw = JoystickClaw(**kwargs)
        claw.enable(sample(), measured, (1704, 3231), 1)
        return claw

    def test_start_requires_center_and_fresh_released_stick(self):
        for s in (sample(y=0), sample(pressed=True), sample(sequence=0), sample(seen=0)):
            claw = JoystickClaw()
            with self.assertRaises(ValueError):
                claw.enable(s, 2000, (1704, 3231), 1)
            self.assertFalse(claw.enabled)

    def test_axis_deadzone_speed_and_reverse(self):
        middle = (1704+3231)/2
        claw = self.armed(measured=middle)
        self.assertEqual(claw.step(sample(y=1880,x=4095),middle,0,.05,1),middle)
        high = claw.step(sample(y=0),middle,0,.05,1)
        self.assertAlmostEqual(high,middle+(3231-1704)*.2*.05)
        reverse = self.armed(measured=middle,reverse=True)
        self.assertLess(reverse.step(sample(y=0),middle,0,.05,1),middle)
        x_control = self.armed(measured=middle,axis="x")
        self.assertEqual(x_control.step(sample(y=4095),middle,0,.05,1),middle)
        self.assertGreater(x_control.step(sample(x=0),middle,0,.05,1),middle)

    def test_absolute_endpoints_and_center_are_reached_without_jumps(self):
        claw = self.armed()
        measured = 2000
        for raw,expected in ((0,3231),(4095,1704),(1810,(1704+3231)/2)):
            for _ in range(110):
                target = claw.step(sample(y=raw),measured,0,.05,1)
                self.assertLessEqual(abs(target-measured),(3231-1704)*.2*.05+1e-9)
                measured = target
            self.assertAlmostEqual(measured,expected)
        self.assertEqual(claw.step(sample(y=1800),measured+3,0,.05,1),measured)

    def test_enabling_near_center_does_not_replace_saved_center(self):
        claw = JoystickClaw()
        claw.enable(sample(x=1400,y=1860),2000,(1704,3231),1)
        self.assertEqual(claw.center,1810)
        with self.assertRaises(ValueError):
            claw.enable(sample(y=3000),2000,(1704,3231),1)

    def test_lead_and_calibrated_travel_are_bounded(self):
        claw = self.armed()
        for _ in range(100):
            goal = claw.step(sample(y=0), 2000, 0, 1, 1)
        self.assertLessEqual(goal, 2000+(3231-1704)*.02)
        claw.enable(sample(), 3230, (1704, 3231), 1)
        self.assertEqual(claw.step(sample(y=0), 3230, 0, 1, 1), 3231)

    def test_button_stale_reboot_and_high_load_reject_movement(self):
        for s, load in ((sample(pressed=True), 0), (sample(seen=0), 0),
                        (sample(epoch=1), 0), (sample(), 350), (sample(), -350)):
            claw = self.armed()
            with self.assertRaises(ValueError):
                claw.step(s, 2000, load, .05, 1)

    def test_gripper_validation_and_commands_only_touch_servo_six(self):
        arm = Arm("TEST")
        arm.bus = MagicMock()
        arm.bus.sync_read.return_value = {"gripper": 2000}
        arm.bus.read.side_effect = lambda register, *args, **kwargs: {
            "Homing_Offset": arm.calibration["gripper"].homing_offset, "Operating_Mode": 0
        }[register]
        self.assertEqual(arm.validate_gripper(), 2000)
        arm.bus.sync_write.assert_not_called()
        arm.bus.write.assert_not_called()
        arm.latch_gripper_and_enable(2000)
        self.assertEqual(arm.bus.write.call_args_list[0].args, ("Goal_Position", "gripper", 2000))
        self.assertEqual(arm.bus.write.call_args_list[1].args, ("Torque_Enable", "gripper", 1))
        arm.move_gripper(9999)
        self.assertEqual(arm.bus.write.call_args.args, ("Goal_Position", "gripper", 3231))
        arm.bus.read.side_effect = lambda *args, **kwargs: 9999
        with self.assertRaises(ValueError):
            arm.validate_gripper()

    def test_worker_button_pauses_claw_without_any_body_motion(self):
        hub = Hub(cloud=False)
        worker = RobotWorker(hub, "TEST")
        worker.commands.put("claw_enable")
        snapshots = [sample(seen=time.monotonic()), sample(y=4095, seen=time.monotonic()),
                     sample(pressed=True, seen=time.monotonic())]
        reads = iter(snapshots)
        hub.joystick_snapshot = lambda: next(reads)
        with patch("robot_control.controller.Arm") as factory:
            arm = factory.return_value
            arm.read_load.return_value = 0
            arm.bus.read.return_value = 0
            arm.validate_gripper.return_value = 2000
            arm.gripper_position.return_value = 2000
            arm.limits.return_value = (1704, 3231)
            def hold():
                # The first call occurs before enabling; the second is the button pause.
                if arm.hold_gripper.call_count >= 2:
                    hub.stop.set()
            arm.hold_gripper.side_effect = hold
            worker.run()
            arm.latch_gripper_and_enable.assert_called_once_with(2000)
            arm.move_gripper.assert_called_once()
            arm.move_raw.assert_not_called()
            arm.latch_and_enable.assert_not_called()
            self.assertFalse(worker.claw.enabled)

    def test_user_imu_roles_match_configuration(self):
        worker = RobotWorker(Hub(cloud=False), "TEST")
        self.assertEqual(worker.config["sensors"], {
            "upper_arm": "s3_imu1", "forearm": "s3_imu2", "hand": "wroom"})


if __name__ == "__main__":
    unittest.main()
