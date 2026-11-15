"""Persistent calibration survives app restarts; no GUI, ports or motors."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wearable_test.configuration import load_model,save_model
from wearable_test.io import DemoHub
from wearable_test.model import ArmModel
from wearable_test.retarget import Retargeter

URDF = Path(__file__).resolve().parents[1]/'so101_sim/model/so101_new_calib.urdf'


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.saved = Path(self.directory.name)/'calibration.local.json'
        self.settings = Path(self.directory.name)/'settings.local.json'
        self.hub = DemoHub()
        self.model = ArmModel({'upper_cm':32,'forearm_cm':24})
        self.model.calibrate(self.hub.frame('A'),self.hub.frame('B'))
        for pose in ('C','D','E','F'): self.model.capture_motion_range(pose,self.hub.frame(pose))

    def test_self_contained_config_restores_every_range_and_only_needs_pose_a(self):
        status = save_model(self.model,self.saved)
        self.assertEqual(status['state'],'saved')
        restored,status = load_model(self.settings,self.saved)
        self.assertEqual(status['state'],'loaded')
        self.assertEqual(restored.settings,self.model.settings)
        self.assertEqual(restored.motion_ranges,self.model.motion_ranges)
        self.assertIsNone(restored.reference)
        restored.recenter(self.hub.frame('A'))
        reverse = dict.fromkeys(Retargeter(URDF).limits,False)
        for pose in ('A','C','D','E','F'):
            original = Retargeter(URDF).targets(self.model.geometry(self.hub.frame(pose)),reverse)
            actual = Retargeter(URDF).targets(restored.geometry(self.hub.frame(pose)),reverse)
            for name in original: self.assertAlmostEqual(actual[name],original[name],places=9)

    def test_explicit_axis_or_dimension_change_refuses_incompatible_mounting(self):
        save_model(self.model,self.saved)
        self.settings.write_text(json.dumps({'upper_cm':35}))
        restored,status = load_model(self.settings,self.saved)
        self.assertEqual(status['state'],'error')
        self.assertEqual(restored.settings['upper_cm'],35)
        self.assertIsNone(restored.mounts)

    def test_failed_save_keeps_prior_file_and_demo_ignores_saved_hardware(self):
        save_model(self.model,self.saved)
        previous = self.saved.read_bytes()
        self.model.motion_ranges['pan_left_deg'] = 25
        with patch.object(Path,'replace',side_effect=OSError('disk busy')):
            with self.assertRaises(OSError): save_model(self.model,self.saved)
        self.assertEqual(self.saved.read_bytes(),previous)
        demo,status = load_model(self.settings,self.saved,demo=True)
        self.assertEqual(status['state'],'demo')
        self.assertIsNone(demo.mounts)
        self.assertEqual(self.saved.read_bytes(),previous)

    def test_corrupt_saved_file_is_reported_without_replacing_it(self):
        self.saved.write_text('{broken')
        model,status = load_model(self.settings,self.saved)
        self.assertEqual(status['state'],'error')
        self.assertIsNone(model.mounts)
        self.assertEqual(self.saved.read_text(),'{broken')
