# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyzmq>=26", "pyserial>=3.5"]
# ///
"""Real calibration logic and transport with fake sensors; no hardware."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import zmq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wearable_test.console import CalibrationConsole
from wearable_test.io import DemoHub, axis_q
from wearable_test.model import ArmModel
from wearable_test.run import App
from bno085_reader.joystick_calibration import JoystickCalibration
from robot_control.haptics import MOTORS
from robot_control.joints import JOINTS


FIXTURE = Path(__file__).resolve().parents[1] / 'frontend/src/telemetry/fixtures/calibration_full.json'


def shape(value):
    """Keys and value kinds, for comparing a live status with the frontend's fixture."""
    if isinstance(value, dict): return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list): return [shape(item) for item in value]
    if isinstance(value, bool): return 'bool'
    if isinstance(value, (int, float)): return 'number'
    return 'null' if value is None else type(value).__name__


class Value:
    def __init__(self): self.value = 'Capture A.'
    def get(self): return self.value
    def set(self, value): self.value = value


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.app = app = object.__new__(App)
        app.hub, app.model, app.message = DemoHub(), ArmModel(), Value()
        app.capture = app.pose_a = app.pose_b = app.robot_preview = None
        app.physical, app.feedback, app.root = MagicMock(), MagicMock(), MagicMock()
        app.calibration_revision, app.recovery_reason, app.epochs = 0, '', (0, 0)
        app.geometry = app.model.geometry()
        app.canvas, app.robot_button, app.recenter_button = MagicMock(), MagicMock(), MagicMock()
        app.next_status_ui = float('inf')
        app.reverse = dict.fromkeys(JOINTS, False)
        app.motor_percent = {key: 40. for key in MOTORS}
        app.joystick_calibration = JoystickCalibration()
        self.context = zmq.Context()
        self.sender = self.context.socket(zmq.PUB)
        self.sender.bind('tcp://127.0.0.1:0')
        app.console = self.console = CalibrationConsole(app, self.sender.last_endpoint.decode(), 'tcp://127.0.0.1:0')

    def tearDown(self):
        self.console.close()
        self.context.destroy(linger=0)

    def command(self, action):
        return dict(type='calibrate_arm', action=action, session=self.console.session,
                    revision=self.console.revision, request_id=uuid4().hex, t=time.time())

    def send(self, action):
        self.console.command(self.command(action))
        return self.console.request

    def status(self):
        snapshot = self.app.hub.snapshot()
        return self.console.status(snapshot, time.monotonic())

    def finish_capture(self):
        app = self.app
        pose = app.capture['pose']
        frame = app.hub.frame('A' if pose == 'R' else pose)
        app.capture['samples'] = {sensor: [q] * 20 for sensor, q in frame.items()}
        app.capture['start'] = time.monotonic() - 5.1
        snapshot = app.hub.snapshot()
        app.capture_tick(snapshot, time.monotonic())

    def test_two_pose_capture_pauses_and_persists_without_enabling(self):
        self.assertEqual(self.status()['phase'], 'needs_pose_a')
        self.assertTrue(self.send('capture_a')['ok'])
        self.assertEqual(self.status()['phase'], 'preparing')
        self.app.capture['start'] -= 3.5
        self.assertEqual(self.status()['phase'], 'sampling')
        self.finish_capture()
        self.assertEqual(self.status()['phase'], 'needs_pose_b')
        self.assertTrue(self.send('capture_b')['ok'])
        self.app.hub.demo = False
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'mounting.json'
            with patch('wearable_test.run.CALIBRATION', target): self.finish_capture()
            restored = ArmModel()
            restored.restore_mounting(json.loads(target.read_text()))
        self.assertEqual(self.status()['phase'], 'ready')
        self.assertFalse(restored.calibrated)
        self.app.physical.pause.assert_called()
        self.app.feedback.stop.assert_called()
        self.app.physical.enable.assert_not_called()
        self.app.physical.enable_arm.assert_not_called()
        self.app.root.lift.assert_not_called()

    def test_recenter_cancel_preserves_mounting_without_resuming(self):
        app = self.app
        app.model.calibrate(app.hub.frame('A'), app.hub.frame('B'))
        mounts = app.model.mounting_record()
        app.model.invalidate_reference()
        self.assertEqual(self.status()['phase'], 'needs_recenter')
        self.assertTrue(self.send('recenter')['ok'])
        self.assertTrue(self.send('cancel')['ok'])
        self.assertIsNone(app.capture)
        self.assertFalse(app.model.calibrated)
        self.assertEqual(app.model.mounting_record(), mounts)
        self.send('recenter')
        self.finish_capture()
        self.assertTrue(app.model.calibrated)
        self.assertEqual(app.model.mounting_record(), mounts)

    def test_refuses_missing_prerequisites_duplicate_and_expired_commands(self):
        self.assertFalse(self.send('capture_b')['ok'])
        self.assertFalse(self.send('recenter')['ok'])
        for changes in ({'t': time.time() - 3}, {'t': float('nan')}, {'t': True},
                        {'t': time.time() + 10}, {'session': 'old'}, {'action': 'enable_arm'}):
            self.console.command({**self.command('capture_a'), **changes})
            self.assertFalse(self.console.request['ok'])
            self.assertIsNone(self.app.capture)
        command = self.command('capture_a')
        self.console.command(command)
        self.assertTrue(self.console.request['ok'])
        start = self.app.capture['start']
        self.console.command(command)
        self.assertEqual(self.app.capture['start'], start)
        self.assertFalse(self.send('capture_a')['ok'])

    def test_local_stop_rejects_queued_capture_but_allows_stale_revision_stop(self):
        queued = self.command('capture_a')
        self.app.stop_all()
        self.console.command(queued)
        self.assertFalse(self.console.request['ok'])
        self.assertIsNone(self.app.capture)
        self.send('capture_a')
        self.console.command({**self.command('cancel'), 'revision': 0})
        self.assertTrue(self.console.request['ok'])
        self.assertIsNone(self.app.capture)

    def test_stale_and_reset_sensors_invalidate_capture(self):
        self.send('capture_a')
        snapshot = self.app.hub.snapshot()
        snapshot['s3']['samples']['s3_imu1']['seen'] -= 1
        status = self.console.status(snapshot, time.monotonic())
        self.assertFalse(status['input_fresh'])
        self.assertFalse(status['sensors'][0])
        self.app.capture_tick(snapshot, time.monotonic())
        self.assertIsNone(self.app.capture)
        self.assertFalse(self.app.model.calibrated)
        self.send('capture_a')
        self.app.hub.boards['s3'].reset('test reset')
        self.app.tick_body()
        self.assertIsNone(self.app.capture)
        self.assertFalse(self.app.model.calibrated)

    def test_unstable_capture_reports_failure_and_can_retry(self):
        self.send('capture_a')
        self.app.capture['start'] -= 6
        self.app.capture['samples'] = {sensor: [axis_q(0, 0)] * 20 + [axis_q(0, 1)]
                                       for sensor in self.app.hub.frame('A')}
        snapshot = self.app.hub.snapshot()
        self.app.capture_tick(snapshot, time.monotonic())
        self.assertIn('moved', self.app.message.get())
        self.assertEqual(self.status()['phase'], 'needs_pose_a')
        self.assertTrue(self.send('capture_a')['ok'])

    def test_real_zmq_command_and_status(self):
        receiver = self.context.socket(zmq.SUB)
        receiver.setsockopt_string(zmq.SUBSCRIBE, 'calibration ')
        receiver.connect(self.console.publisher.last_endpoint.decode())
        command = self.command('capture_a')
        deadline, received = time.monotonic() + 3, None
        while time.monotonic() < deadline:
            self.sender.send_string('command ' + json.dumps(command))
            self.console.poll()
            snapshot = self.app.hub.snapshot()
            self.console.publish(snapshot, time.monotonic())
            if receiver.poll(20):
                received = json.loads(receiver.recv_string().split(' ', 1)[1])
                if received['request'] is not None: break
        self.assertIsNotNone(received)
        self.assertEqual(received['request']['id'], command['request_id'])
        self.assertTrue(received['request']['ok'])
        self.assertEqual(received['phase'], 'preparing')
        receiver.close(linger=0)

    def test_range_captures_require_reference_and_persist_across_recenter(self):
        self.assertFalse(self.send('capture_elbow')['ok'])
        self.assertFalse(self.send('capture_shoulder')['ok'])
        self.assertFalse(self.send('capture_pan_left')['ok'])
        self.assertFalse(self.send('capture_pan_right')['ok'])
        self.send('capture_a')
        self.finish_capture()
        self.send('capture_b')
        self.finish_capture()
        for action,field,expected in (('capture_elbow','elbow_max_deg',125),('capture_shoulder','shoulder_max_deg',80),
                                       ('capture_pan_left','pan_left_deg',40),('capture_pan_right','pan_right_deg',30)):
            self.assertTrue(self.send(action)['ok'])
            self.finish_capture()
            self.assertAlmostEqual(self.status()['motion_ranges'][field],expected)
        ranges = dict(self.app.model.motion_ranges)
        restored = ArmModel()
        restored.restore_mounting(json.loads(json.dumps(self.app.model.mounting_record())))
        self.assertFalse(restored.calibrated)
        restored.recenter(self.app.hub.frame('A'))
        self.assertEqual(restored.motion_ranges,ranges)
        self.send('recenter')
        self.finish_capture()
        self.assertEqual(self.app.model.motion_ranges,ranges)
        self.send('capture_a')
        self.assertEqual(self.app.model.motion_ranges,{})

    def test_bad_range_capture_retains_previous_good_calibration(self):
        model,hub = self.app.model,self.app.hub
        model.calibrate(hub.frame('A'),hub.frame('B'))
        model.capture_motion_range('C',hub.frame('C'))
        original = dict(model.motion_ranges)
        for pose,frame in (('C',hub.frame('A')),('D',hub.frame('B')),('D',hub.frame('C')),
                           ('E',hub.frame('A')),('E',hub.frame('F')),('F',hub.frame('E')),('F',hub.frame('B'))):
            with self.assertRaises(ValueError): model.capture_motion_range(pose,frame)
            self.assertEqual(model.motion_ranges,original)
        record = model.mounting_record()
        for invalid in ({'elbow_max_deg':0},{'shoulder_max_deg':float('nan')},{'shoulder_min_deg':-5},
                        {'pan_left_deg':True},{'pan_right_deg':float('nan')},{'pan_left_deg':14},{'pan_right_deg':151}):
            with self.assertRaises(ValueError): ArmModel().restore_mounting({**record,'motion_ranges':invalid})

    def physical_state(self):
        state = dict(connected=True,seen=time.monotonic(),enabled=False,claw_enabled=False,
                     tuning_requested=0,tuning_applied=0,controls=dict(speed_deg_s=240,claw_speed_per_s=2,
                     max_load=650,gripper_max_load=350,load_stops_enabled=False),
                     slider=dict(referenced=False,enabled=False,pos=None,target=None,detail='Reference slider.'))
        self.app.physical.snapshot.return_value = state
        self.app.physical.tune.return_value = 1
        return state

    def test_tuning_validates_transport_and_waits_for_worker_confirmation(self):
        state = self.physical_state()
        command = {**self.command('unused'),'type':'tune_robot','changes':{'max_load':750},'revision':0}
        self.console.command(command)
        self.app.physical.tune.assert_called_once_with({'max_load':750},revision=0)
        self.assertFalse(self.status()['tuning']['request']['applied'])
        self.assertEqual(self.status()['tuning']['values']['max_load'],650)
        self.console.command(command)
        self.app.physical.tune.assert_called_once()
        state.update(tuning_requested=1,tuning_applied=1)
        state['controls']['max_load'] = 750
        self.assertTrue(self.status()['tuning']['request']['applied'])
        self.assertEqual(self.status()['tuning']['values']['max_load'],750)
        for changes in ({'t':time.time()-3},{'session':'old'},{'revision':True},{'changes':{'Torque_Limit':1000}},{'changes':{}}):
            self.console.command({**command,'request_id':uuid4().hex,**changes})
            self.assertFalse(self.status()['tuning']['request']['ok'])
        state['seen'] -= 1
        self.console.command({**command,'request_id':uuid4().hex})
        self.assertFalse(self.status()['tuning']['request']['ok'])
        self.app.physical.tune.assert_called_once()
        self.app.physical.request.assert_not_called()

    def test_slider_commands_expire_and_stop_invalidates_queued_enable(self):
        self.physical_state()
        command = {**self.command('enable'),'type':'control_slider'}
        self.console.command({**command,'action':'zero'})
        self.app.physical.request.assert_called_once_with('slider_zero')
        self.console.command({**command,'request_id':uuid4().hex})
        self.assertFalse(self.status()['slider']['request']['ok'])
        command = {**self.command('enable'),'type':'control_slider'}
        self.app.stop_all()
        self.console.command(command)
        self.assertFalse(self.status()['slider']['request']['ok'])
        self.app.physical.request.assert_called_once()

    def test_reconnect_checks_session_expiry_and_never_requests_enable(self):
        self.physical_state()
        command = {**self.command('unused'),'type':'reconnect_robot'}
        self.console.command(command)
        self.app.physical.reconnect.assert_called_once()
        self.assertTrue(self.status()['tuning']['reconnect_request']['ok'])
        self.console.command(command)
        self.app.physical.reconnect.assert_called_once()
        for changes in ({'session':'old'},{'t':time.time()-3},{'revision':True}):
            self.console.command({**command,'request_id':uuid4().hex,**changes})
            self.assertFalse(self.status()['tuning']['reconnect_request']['ok'])
        self.app.physical.request.assert_not_called()

    def test_replay_checks_transport_and_waits_for_worker_acknowledgment(self):
        state = self.physical_state()
        state['replay'] = dict(phase='ready',revision=2,duration_s=1,elapsed_s=0,samples=51,
                               includes_slider=False,detail='Ready',request=None)
        command = {**self.command('play'),'type':'replay_robot','replay_revision':2}
        self.console.command(command)
        self.app.physical.request_replay.assert_called_once_with('play',revision=2,request_id=command['request_id'])
        self.assertFalse(self.status()['replay']['request']['applied'])
        self.console.command(command)
        self.app.physical.request_replay.assert_called_once()
        state['replay']['request'] = dict(id=command['request_id'],ok=True,applied=True,detail='Returning')
        self.assertTrue(self.status()['replay']['request']['applied'])
        for changes in ({'session':'old'},{'t':time.time()-3},{'revision':True},{'replay_revision':True},{'action':'invalid'}):
            self.console.command({**command,'request_id':uuid4().hex,**changes})
            self.assertFalse(self.status()['replay']['request']['ok'])
        state['seen'] -= 1
        self.console.command({**command,'request_id':uuid4().hex})
        self.assertFalse(self.status()['replay']['request']['ok'])
        self.app.physical.request_replay.assert_called_once()
        with patch.object(self.app,'stop_all') as stop:
            self.console.command({**command,'request_id':uuid4().hex,'action':'stop','revision':-1})
            stop.assert_called_once()
        self.app.physical.request.assert_not_called()

    def test_grasp_settings_use_worker_confirmation_and_publish_holding_state(self):
        state = self.physical_state()
        state['grasp'] = dict(holding=True,load=140,detail='Holding object')
        command = {**self.command('unused'),'type':'tune_robot','revision':0,
                   'changes':{'gripper_contact_load':200,'gripper_hold_load':140}}
        self.console.command(command)
        self.app.physical.tune.assert_called_once_with(command['changes'],revision=0)
        self.assertFalse(self.status()['tuning']['request']['applied'])
        self.assertEqual(self.status()['tuning']['grasp'],state['grasp'])


    # --- Browser parity: robot control, response tuning, settings, directions, coins, status ---

    def full_state(self, **changes):
        """A realistic worker snapshot, as WearableRobot.snapshot() returns it."""
        state = self.physical_state()
        lead = dict(shoulder_pan=30., shoulder_lift=40., elbow_flex=40., wrist_flex=30., wrist_roll=30.)
        tuning = dict(speed_deg_s=240., response_ms=5., control_hz=100., joint_lead_deg=lead)
        state.update(can_enable=True, aligning=False, alignment_progress='', message='COM6: connected, read only.',
                     input_status='Wearable input LIVE', loop_hz=99.6, read_ms=3.2, limited=['elbow_flex'],
                     positions={name: 2048 for name in JOINTS}, desired={name: 2100 for name in JOINTS[:-1]},
                     targets={}, loads={name: 12 for name in JOINTS}, torque={name: True for name in JOINTS},
                     flags={'elbow_flex': 'RATE'}, grip_hold_active=False, port='COM6', mapping_mode='absolute',
                     alignment_deg_s=90., tuning=tuning, tuning_pending=None, startup_tuning=dict(tuning),
                     matching={name: dict(actual_deg=1.25, preview_deg=-3.5) for name in JOINTS[:-1]},
                     replay=dict(phase='empty', revision=0, duration_s=0, elapsed_s=0, samples=0,
                                 includes_slider=False, detail='', request=None),
                     enable_request=None)
        state.update(changes)
        return state

    def robot_command(self, action, **changes):
        return {**self.command('unused'), 'type': 'control_robot', 'action': action, **changes}

    def calibrate_demo(self):
        self.app.model.calibrate(self.app.hub.frame('A'), self.app.hub.frame('B'))

    def test_status_is_json_safe_and_small_with_and_without_robot(self):
        for physical in (None, MagicMock()):
            self.app.physical = physical
            json.dumps(self.status(), allow_nan=False)
        self.app.physical = MagicMock()
        state = self.full_state(loop_hz=float('nan'), read_ms=float('inf'))
        state['matching']['elbow_flex']['preview_deg'] = float('-inf')
        self.calibrate_demo()
        status = self.status()
        text = json.dumps(status, separators=(',', ':'), allow_nan=False)
        self.assertLess(len(text), 8192)
        self.assertIsNone(status['physical']['loop_hz'])
        self.assertIsNone(status['physical']['joints'][2]['preview_deg'])
        self.assertEqual(status['physical']['joints'][2]['flag'], 'RATE')
        self.assertAlmostEqual(status['physical']['joints'][0]['gap_deg'], 52*360/4095, places=1)
        self.assertEqual(status['response']['profile'], 4)
        self.assertEqual([p['name'] for p in status['response']['profiles']][0], 'Precise')
        self.assertEqual(len(status['coins']['motors']), 4)
        self.assertEqual(len(status['wearable']['imus']), 3)
        self.assertEqual(len(status['geometry']['joints']), 4)
        self.assertTrue(status['physical']['can_enable_arm'])

    def test_broken_status_section_is_dropped_and_logged_once(self):
        with patch.object(self.console, 'coins_status', side_effect=KeyError('motors')):
            with patch('wearable_test.console.LOG') as log:
                for _ in range(3): status = self.status()
        self.assertIsNone(status['coins'])
        self.assertEqual(status['phase'], 'needs_pose_a')
        self.assertEqual(log.exception.call_count, 1)

    def test_stop_needs_no_session_revision_or_clock(self):
        with patch.object(self.app, 'stop_all') as stop:
            self.console.command(self.robot_command('stop', session='other', revision=-5, t=0))
            stop.assert_called_once()
        self.assertTrue(self.status()['control_request']['applied'])

    def test_enable_is_gated_then_queued_once_and_reports_the_worker_outcome(self):
        state = self.full_state()
        self.assertFalse(self.status()['physical']['can_enable_arm'])  # Not calibrated yet.
        self.console.command(self.robot_command('enable_arm'))
        self.assertFalse(self.status()['control_request']['ok'])
        self.calibrate_demo()
        command = self.robot_command('enable_arm')
        self.console.command(command)
        self.console.command(command)
        self.app.physical.request.assert_called_once_with('enable_arm', request_id=command['request_id'])
        self.assertFalse(self.status()['control_request']['applied'])
        state['enable_request'] = dict(id=command['request_id'], ok=False, detail='Enable refused: high arm load')
        self.assertEqual(self.status()['control_request']['detail'], 'Enable refused: high arm load')
        state['enable_request'] = dict(id=command['request_id'], ok=True, detail='ALIGNING')
        self.assertTrue(self.status()['control_request']['applied'])
        for action, changes in (('enable_claw', {'revision': self.console.revision + 1}), ('enable_claw', {'session': 'old'}),
                                ('enable_claw', {'t': time.time() - 3}), ('jump', {})):
            self.console.command(self.robot_command(action, **changes))
            self.assertFalse(self.status()['control_request']['ok'])
        state['seen'] -= 1
        self.console.command(self.robot_command('enable_claw'))
        self.assertIn('stale', self.status()['control_request']['detail'])
        state['seen'] = time.monotonic()
        state['can_enable'] = False
        self.console.command(self.robot_command('enable_claw'))
        self.assertFalse(self.status()['control_request']['ok'])
        state['can_enable'] = True
        self.send('recenter')
        self.console.command(self.robot_command('enable_claw'))
        self.assertIn('capture', self.status()['control_request']['detail'])
        self.app.physical.request.assert_called_once()

    def test_response_profiles_and_startup_values_map_in_python(self):
        from robot_control.tuning import response_profile
        state = self.full_state()
        base = {**self.command('unused'), 'type': 'tune_robot', 'revision': 0}
        self.console.command({**base, 'profile': 2})
        self.app.physical.tune.assert_called_once_with(response_profile(2), revision=0)
        self.console.command({**base, 'request_id': uuid4().hex, 'startup': True})
        self.app.physical.tune.assert_called_with(state['startup_tuning'], revision=0)
        leads = {'response_ms': 20, 'joint_lead_deg': {'elbow_flex': 12}}
        self.console.command({**base, 'request_id': uuid4().hex, 'changes': leads})
        self.app.physical.tune.assert_called_with(leads, revision=0)
        calls = self.app.physical.tune.call_count
        for changes in ({'profile': True}, {'profile': 5}, {'profile': -1}, {'startup': 1},
                        {'profile': 1, 'changes': {'max_load': 700}}, {'changes': {'servo_pid': 1}}):
            self.console.command({**base, 'request_id': uuid4().hex, **changes})
            self.assertFalse(self.status()['tuning']['request']['ok'])
        self.assertEqual(self.app.physical.tune.call_count, calls)

    def arm_settings(self, **changes):
        settings = {'upper_cm': 31, 'forearm_cm': 25.5, 'hand_cm': 18, 'bicep_from_elbow_pct': 25,
                    'forearm_from_wrist_pct': 33.3, 'hand_from_wrist_pct': 50,
                    'axes': {'upper': 'Auto', 'forearm': '+X', 'hand': 'Auto'}}
        settings.update(changes)
        return settings

    def test_arm_settings_strictly_validated_then_saved_and_calibration_cleared(self):
        self.calibrate_demo()
        base = {**self.command('unused'), 'type': 'configure_arm'}
        invalid = (self.arm_settings(upper_cm=True), self.arm_settings(upper_cm='31'),
                   self.arm_settings(hand_cm=float('nan')), self.arm_settings(hand_cm=4),
                   self.arm_settings(hand_from_wrist_pct=101), self.arm_settings(extra=1),
                   self.arm_settings(axes={'upper': '+W', 'forearm': 'Auto', 'hand': 'Auto'}),
                   self.arm_settings(axes={'upper': 'Auto', 'forearm': 'Auto'}), None)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'settings.json'
            with patch('wearable_test.run.SETTINGS', target):
                for settings in invalid:
                    self.console.command({**base, 'request_id': uuid4().hex, 'settings': settings})
                    self.assertFalse(self.status()['arm_settings']['request']['ok'])
                    self.assertFalse(target.exists())
                self.assertTrue(self.app.model.calibrated)
                revision = self.console.revision
                self.console.command({**base, 'request_id': uuid4().hex, 'revision': revision, 'settings': self.arm_settings()})
                status = self.status()
                self.assertTrue(status['arm_settings']['request']['ok'])
                self.assertEqual(json.loads(target.read_text())['upper_cm'], 31)
        self.assertFalse(self.app.model.calibrated)
        self.assertEqual(status['arm_settings']['values']['axes']['forearm'], '+X')
        self.assertEqual(status['phase'], 'needs_pose_a')
        self.assertGreater(self.console.revision, revision)
        self.app.physical.pause.assert_called()

    def test_joint_direction_reaches_robot_reference_and_waits_for_hold(self):
        state = self.full_state()
        base = {**self.command('unused'), 'type': 'set_joint_direction', 'joint': 'wrist_roll', 'reverse': True}
        state['enabled'] = True
        self.console.command(base)
        self.assertFalse(self.status()['directions']['request']['ok'])
        self.assertFalse(self.app.reverse['wrist_roll'])
        state['enabled'] = False
        for changes in ({'joint': 'elbow'}, {'reverse': 1}):
            self.console.command({**base, 'request_id': uuid4().hex, **changes})
            self.assertFalse(self.status()['directions']['request']['ok'])
        self.console.command({**base, 'request_id': uuid4().hex, 'revision': self.console.revision})
        self.assertTrue(self.status()['directions']['request']['ok'])
        self.assertTrue(self.status()['directions']['reverse']['wrist_roll'])
        self.app.tick_body()
        self.assertTrue(self.app.physical.submit_reference.call_args.args[5]['wrist_roll'])

    def test_coin_bench_maps_motor_order_and_stop_survives_a_local_stop(self):
        base = {**self.command('unused'), 'type': 'test_haptic_motor', 'motor': 2, 'percent': 55}
        with patch.object(self.app.hub, 'motor') as motor, patch.object(self.app.hub, 'stop') as stop:
            self.console.command({**base, 'action': 'test'})
            motor.assert_called_once_with('s3', 2, 55., 1)
            self.app.feedback.stop.assert_called()
            self.console.command({**base, 'request_id': uuid4().hex, 'action': 'run', 'motor': 0})
            motor.assert_called_with('wroom', 1, 55., 10)
            self.app.stop_all()
            self.console.command({**base, 'request_id': uuid4().hex, 'action': 'run'})
            self.assertFalse(self.status()['coins']['request']['ok'])
            self.console.command({**base, 'request_id': uuid4().hex, 'action': 'stop'})
            stop.assert_called_with('s3', 2)
            for changes in ({'motor': 4}, {'motor': True}, {'percent': 101}, {'action': 'buzz'}):
                self.console.command({**base, 'request_id': uuid4().hex, 'action': 'intensity', **changes})
                self.assertFalse(self.status()['coins']['request']['ok'])
        self.assertEqual(motor.call_count, 2)
        self.assertEqual(self.status()['coins']['motors'][2]['percent'], 55)

    def test_handler_failure_never_reaches_the_ui_tick(self):
        failing = MagicMock(side_effect=RuntimeError('boom'))
        self.console.handlers['calibrate_arm'] = failing
        deadline = time.monotonic() + 3
        with patch('wearable_test.console.LOG'):
            while time.monotonic() < deadline and not failing.called:
                self.sender.send_string('command ' + json.dumps(self.command('capture_a')))
                self.console.poll()
                time.sleep(.02)
        self.assertTrue(failing.called)

    def test_status_matches_the_frontend_fixture(self):
        """The browser validates every section; this file is what its tests check against.

        Regenerate after an intended change: set UPDATE_CALIBRATION_FIXTURE=1 and run this test.
        """
        state = self.full_state()
        state['grasp'] = dict(holding=False, load=12, detail='Open; no contact.')
        state['tuning_pending'] = dict(state['tuning'])
        state['enable_request'] = dict(id='enable-1', ok=True, detail='ALIGNING to captured URDF pose (90 deg/s).')
        self.calibrate_demo()
        self.app.geometry = self.app.model.geometry(self.app.hub.frame('C'))
        reply = lambda request_id: dict(id=request_id, ok=True, detail='Done.')
        self.console.request = reply('capture-1')
        self.console.tuning_request = dict(reply('tune-1'), revision=1)
        self.console.reconnect_request = reply('reconnect-1')
        self.console.slider_request = reply('slider-1')
        self.console.control_request = dict(reply('enable-1'), applied=False)
        self.console.arm_settings_request = reply('arm-1')
        self.console.directions_request = reply('direction-1')
        self.console.coins_request = reply('coin-1')
        status = self.status()
        for section in ('physical', 'response', 'arm_settings', 'directions', 'coins', 'wearable', 'geometry', 'control_request'):
            self.assertIsNotNone(status[section], section)
        self.assertTrue(status['control_request']['applied'])
        text = json.dumps(status, indent=2, allow_nan=False) + '\n'
        if os.environ.get('UPDATE_CALIBRATION_FIXTURE'): FIXTURE.write_text(text, encoding='utf-8')
        self.assertEqual(shape(json.loads(FIXTURE.read_text(encoding='utf-8'))), shape(status))


if __name__ == '__main__': unittest.main()
