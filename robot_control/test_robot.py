import math
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"bno085_reader"))

from robot_control.arm import Arm, BODY, choose_port
from robot_control.controller import RobotWorker, bounded_step
from robot_control.load_feedback import LoadFeedback
from dashboard import Hub


class FeedbackTests(unittest.TestCase):
    def test_continuous_intensity_in_both_directions_and_reverse(self):
        def output(load, reverse=False):
            f = LoadFeedback(reverse=reverse)
            f.update(load, 1)
            return f.output(1)
        for load in (-200, -60, 60, 200):
            f = LoadFeedback()
            expected = output(load)[0]
            for step in range(201):
                now = 1+step*.02
                f.update(load, now)
                self.assertEqual(f.output(now)[0], expected, 'Steady load must have no buzz gaps')
            f.update(-load, 5.02)
            self.assertEqual(f.output(5.02)[0], expected, 'Direction change must not introduce a gap')
        self.assertIn("LEFT", output(200, True)[1])
        self.assertGreater(output(95)[0], output(60)[0])
        self.assertEqual(output(1023)[0], 127)
        self.assertEqual(output(20)[0], 0)

    def test_requested_load_boundaries_in_both_directions(self):
        for sign in (-1, 1):
            for magnitude, expected in ((0, 0), (24.99, 0), (25, 30), (60, 78), (95, 127), (200, 127)):
                with self.subTest(sign=sign, magnitude=magnitude):
                    f = LoadFeedback()
                    f.update(sign*magnitude, 1)
                    self.assertEqual(f.output(1)[0], expected)
            f = LoadFeedback()
            f.update(sign*95, 1)
            f.update(sign*24, 1.03)
            self.assertEqual(f.output(1.03)[0], 0, 'No hysteresis/filter tail below 25')
            f.update(sign*95, 1.06)
            self.assertEqual(f.output(1.06)[0], 127, 'Full intensity on the next high-load sample')

    def test_stale_invalid_and_tare_stop_output(self):
        f = LoadFeedback()
        f.update(400, 0)
        self.assertEqual(f.output(.31)[0], 0)
        f.zero(400)
        f.update(400, 1)
        self.assertEqual(f.output(1)[0], 0)
        with self.assertRaises(ValueError):
            f.update(float('nan'), 1.1)
        self.assertEqual(f.output(1.1)[0], 0)

    def test_load_mode_ignores_imu_rotation(self):
        hub = Hub(cloud=False)
        worker = RobotWorker(hub, "TEST")
        worker.feedback.update(400, 1)
        before = worker.haptic_output(1)
        hub.ingest("wroom", "0,1,0,0")
        self.assertEqual(before, worker.haptic_output(1))


class RobotTests(unittest.TestCase):
    def test_dashboard_routes_load_commands_and_stops_while_imu_keeps_streaming(self):
        hub = Hub(cloud=False)
        calls = []
        writes = []
        def source(now):
            calls.append(now)
            return (50 if len(calls) < 5 else 0), 'test load'
        hub.haptic_source = source
        class Port:
            def __init__(self, *args, **kwargs):
                self.index = 0
                self.acks = []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def open(self): pass
            def close(self): pass
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, value):
                writes.append(value)
                if value.startswith(b'VIBRATE'):
                    self.acks.append(b'# HAPTIC ON 300\n')
            def readline(self):
                self.index += 1
                if self.index == 1:
                    return b'# DEVICE WROOM_IO_V1 IMU=1 DRV2605L=1 MOTOR=ERM\n'
                if self.index == 2:
                    hub.commands.put(('enable', (1,0,0,0)))
                if self.index > 18:
                    hub.stop.set()
                if self.acks:
                    return self.acks.pop(0)
                return b'0,1,0,0\n'
        with patch('dashboard.serial.Serial', Port):
            hub.read_port('wroom', 'COM7')
        self.assertIn(b'VIBRATE,300,50\n', writes)
        on = writes.index(b'VIBRATE,300,50\n')
        self.assertIn(b'STOP\n', writes[on+1:])
        self.assertGreater(hub.snapshot()['wroom']['sequence'], 10)

    def test_port_selection_excludes_esp32s_and_rejects_ambiguity(self):
        def port(name, vid=1):
            p = MagicMock()
            p.device, p.vid = name, vid
            return p
        with patch('serial.tools.list_ports.comports', return_value=[port('COM3', None), port('COM7'), port('COM8')]):
            with self.assertRaises(ValueError):
                choose_port()
            with self.assertRaises(ValueError):
                choose_port('COM8')
        with patch('serial.tools.list_ports.comports', return_value=[port('COM7'), port('COM8'), port('COM6')]):
            self.assertEqual(choose_port(), 'COM6')

    def test_connect_and_close_do_not_write_or_disable_torque(self):
        arm = Arm('TEST')
        arm.bus = MagicMock()
        arm.connect()
        arm.close()
        arm.bus.write.assert_not_called()
        arm.bus.sync_write.assert_not_called()
        arm.bus.disconnect.assert_called_once_with(disable_torque=False)

    def test_saved_calibration_mismatch_blocks_motion(self):
        arm = Arm('TEST')
        arm.bus = MagicMock()
        arm.bus.sync_read.return_value = {n: (arm.calibration[n].range_min+arm.calibration[n].range_max)//2 for n in BODY}
        arm.bus.read.return_value = 9999
        with self.assertRaisesRegex(ValueError, 'does not match'):
            arm.validate_motion()
        arm.bus.sync_write.assert_not_called()

    def test_motion_rate_tracking_lead_and_joint_limits(self):
        anchor = dict.fromkeys(BODY, 2000.)
        offsets = dict.fromkeys(BODY, math.pi)
        limits = dict.fromkeys(BODY, (100, 3500))
        out = bounded_step(anchor, offsets, anchor, anchor, limits, .05)
        self.assertAlmostEqual(out['shoulder_pan'], 2000+10*4095/360*.05)
        far_previous = dict.fromkeys(BODY, 3000.)
        out = bounded_step(anchor, offsets, far_previous, anchor, limits, 10)
        self.assertLessEqual(out['shoulder_pan'], 2000+4*4095/360)
        limits['shoulder_pan'] = (100, 2001)
        self.assertEqual(bounded_step(anchor, offsets, anchor, anchor, limits, .1)['shoulder_pan'], 2001)

    def test_bus_failure_invalidates_haptic_output(self):
        hub = Hub(cloud=False)
        worker = RobotWorker(hub, 'TEST')
        worker.feedback.update(400, time.monotonic())
        with patch('robot_control.controller.Arm') as mock:
            mock.return_value.read_load.side_effect = ConnectionError('unplugged')
            worker.run()
        self.assertEqual(worker.haptic_output(time.monotonic())[0], 0)
        self.assertFalse(worker.telemetry()['online'])
        self.assertIn('unplugged', worker.status)


if __name__ == '__main__':
    unittest.main()
