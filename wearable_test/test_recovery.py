"""Reset -> mounting retained -> one known-pose recenter -> live robot regression."""
import copy
import json
import time
import unittest
from unittest.mock import MagicMock,patch

from wearable_test.io import DemoHub,axis_q
from wearable_test.model import ArmModel,ROLES
from wearable_test.run import App
from wearable_test import test_robot_preview
from so101_sim.mapper import multiply


class Value:
    def __init__(self,value=""): self.value=value
    def set(self,value): self.value=value
    def get(self): return self.value


class RecoveryTests(unittest.TestCase):
    def test_recenter_preserves_mapping_after_independent_sensor_heading_resets(self):
        hub = DemoHub()
        model = ArmModel()
        model.calibrate(hub.frame("A"),hub.frame("B"))
        before = model.geometry(hub.frame(None,1.2))
        mounts = copy.deepcopy(model.mounts)
        model.invalidate_reference()
        self.assertFalse(model.calibrated)
        self.assertEqual(model.mounts,mounts)
        worlds = {s:axis_q(2,.5+i) for i,s in enumerate(ROLES.values())}
        model.recenter({s:multiply(worlds[s],q) for s,q in hub.frame("A").items()})
        after = model.geometry({s:multiply(worlds[s],q) for s,q in hub.frame(None,1.2).items()})
        for role in ROLES:
            for a,b in zip(before["bases"][role],after["bases"][role]):
                for x,y in zip(a,b): self.assertAlmostEqual(x,y,places=6)

    def test_saved_mounts_require_recenter_and_reject_changed_settings_or_corruption(self):
        hub = DemoHub()
        original = ArmModel()
        original.calibrate(hub.frame("A"),hub.frame("B"))
        record = json.loads(json.dumps(original.mounting_record()))
        restored = ArmModel()
        restored.restore_mounting(record)
        self.assertFalse(restored.calibrated)
        self.assertIsNone(restored.reference)
        self.assertEqual(restored.mounts,original.mounts)
        restored.recenter(hub.frame("A"))
        self.assertTrue(restored.calibrated)
        with self.assertRaises(ValueError): ArmModel({"upper_cm":31}).restore_mounting(record)
        broken = copy.deepcopy(record)
        broken["mounts"]["upper"][0][0] = float("nan")
        with self.assertRaises(ValueError): ArmModel().restore_mounting(broken)
        with self.assertRaises(ValueError): ArmModel().recenter(hub.frame("A"))

    def app(self):
        app = object.__new__(App)
        app.physical,app.physical_panel,app.calibration_revision = None,None,0
        app.feedback = None
        app.hub = DemoHub()
        app.model = ArmModel()
        app.pose_a,app.pose_b = app.hub.frame("A"),app.hub.frame("B")
        app.model.calibrate(app.pose_a,app.pose_b)
        app.geometry = app.model.geometry(app.hub.frame(None,0))
        app.robot_preview = test_robot_preview.PreviewTests().make_preview()
        app.canvas,app.robot_button,app.recenter_button,app.root = (MagicMock() for _ in range(4))
        app.message = Value()
        app.capture,app.recovery_reason,app.tick_error = None,"",None
        app.epochs,app.next_status_ui,app.closed = (0,0),float("inf"),False
        return app

    def test_actual_reset_path_recenter_and_next_pose_update(self):
        app = self.app()
        app.tick_body()
        mounts = copy.deepcopy(app.model.mounts)
        app.hub.boards["s3"].reset("IMU RESET imu1; reports re-enabled")
        app.tick_body()
        self.assertFalse(app.model.calibrated)
        self.assertEqual(app.model.mounts,mounts)
        self.assertIn("IMU RESET imu1",app.robot_preview.hold_reason)
        self.assertTrue(app.robot_preview.running)
        number = app.robot_preview.pose_number
        app.begin_capture("R")
        app.capture["start"] = time.monotonic()-5.1
        app.capture["samples"] = {s:[q]*20 for s,q in app.hub.frame("A").items()}
        app.tick_body()
        self.assertTrue(app.model.calibrated)
        self.assertEqual(app.model.mounts,mounts)
        self.assertGreater(app.robot_preview.pose_number,number)
        self.assertEqual(app.robot_preview.hold_reason,"")

    def test_callback_exception_does_not_kill_update_loop(self):
        app = self.app()
        app.tick_body = MagicMock(side_effect=[RuntimeError("injected callback failure"),None])
        with patch("wearable_test.run.logging.getLogger"):
            app.tick()
            self.assertIn("injected callback failure",app.tick_error)
            app.root.after.assert_called_with(20,app.tick)
            self.assertIn("injected callback failure",app.robot_preview.hold_reason)
            app.tick()
        self.assertIsNone(app.tick_error)
        self.assertEqual(app.root.after.call_count,2)


if __name__ == "__main__": unittest.main()
