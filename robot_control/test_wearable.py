# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Exercise real controller/Arm code against a fake servo bus; never opens a port."""
from copy import deepcopy
import math
import json
import time
import zmq
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from bno085_reader.joystick_calibration import load_calibration
from robot_control.arm import Arm,BODY,JOINTS
from robot_control.telemetry import WearableTelemetry,console_positions
from robot_control.alignment import Alignment
from robot_control.tuning import RESPONSE_PROFILES,response_profile,response_profile_index,apply_values,tuning_values,lead_degrees
from robot_control.urdf_mapping import JointMapping,TICKS_PER_DEGREE
from robot_control.wearable import WearableRobot,bounded_follow,bounded_targets,input_packet,load_config,TICKS_PER_RADIAN
from wearable_test.io import DemoHub
from wearable_test.model import ArmModel
from wearable_test.test_retarget import geometry


class FakeBus:
    def __init__(self,calibration):
        self.calibration = calibration
        self.is_connected = True
        self.positions = {n:(c.range_min+c.range_max)//2 for n,c in calibration.items()}
        self.loads = dict.fromkeys(JOINTS,0)
        self.torque = dict.fromkeys(JOINTS,0)
        self.writes = []
        self.on_read = lambda register:None
        self.homing_error = False
    def connect(self): self.is_connected = True
    def disconnect(self,disable_torque=False): self.is_connected = False
    def set_timeout(self,milliseconds): pass
    def read(self,register,name,**kwargs):
        self.on_read(register)
        if register=="Homing_Offset": return self.calibration[name].homing_offset+int(self.homing_error)
        if register=="Operating_Mode": return 0
        return self.values(register)[name]
    def values(self,register):
        return {"Present_Position":self.positions,"Present_Load":self.loads,"Torque_Enable":self.torque}[register]
    def sync_read(self,register,names,**kwargs):
        self.on_read(register)
        return {name:self.values(register)[name] for name in names}
    def write(self,register,name,value,**kwargs): self.sync_write(register,{name:value},**kwargs)
    def sync_write(self,register,values,**kwargs):
        self.writes.append((register,dict(values)))
        if register=="Torque_Enable": self.torque.update(values)


class PhysicalWearableTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.arm = Arm("TEST")
        self.bus = self.arm.bus = FakeBus(self.arm.calibration)
        self.worker = WearableRobot("TEST",load_calibration(),lambda port:self.arm,lambda:self.now,timer=lambda:self.now)
        self.worker.auto_enable_claw = False  # Arm-path tests; AutoClawHapticsTests covers the default.
        self.publish()

    def publish(self,pose=None,valid=True,epoch=(0,),age=0,joy_y=1810,pressed=False,reverse=None):
        reverse = reverse or dict.fromkeys(JOINTS,False)
        self.packet = dict(geometry=pose or geometry(),valid=valid,reason="recenter needed",epoch=epoch,
                           seen=self.now-age,published=self.now,reverse=reverse,
                           joystick=dict(sequence=1,epoch=0,seen=self.now,x=1360,y=joy_y,pressed=pressed))
        self.worker.submit(self.packet)

    def cycle(self): self.worker.cycle(self.arm,.05)

    def enable(self,command="enable_arm"):
        self.worker.request(command)
        self.cycle()

    def bind_live_source(self):
        demo = DemoHub()
        model = ArmModel()
        model.calibrate(demo.frame('A'),demo.frame('B'))
        template = demo.snapshot()
        values = dict(frame=demo.frame('A'),age=0.,epoch=0,pressed=False)
        def snapshot():
            result = deepcopy(template)
            for board in result.values():
                board['epoch'] = values['epoch']
                board['connected'] = True
                for sensor,sample in board['samples'].items():
                    sample.update(q=values['frame'][sensor],seen=self.now-values['age'])
            result['wroom']['joystick'].update(seen=self.now,pressed=values['pressed'])
            return result
        self.worker.bind_hub(SimpleNamespace(snapshot=snapshot))
        self.worker.submit_reference(model,snapshot(),True,'',1,dict.fromkeys(JOINTS,False),self.now)
        return values,demo

    def test_live_serial_geometry_survives_brief_ui_delay_but_not_ui_hang(self):
        values,demo = self.bind_live_source()
        self.enable()
        first = self.worker.packet['geometry']
        values['frame'] = demo.frame('B')
        self.now += .5  # No Tk update, while actual serial samples stay fresh.
        self.cycle()
        self.assertTrue(self.worker.enabled)
        self.assertNotEqual(self.worker.packet['geometry'],first)
        self.assertEqual(self.worker.packet['published'],self.now)
        self.now += .6
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn('UI unresponsive',self.worker.snapshot()['message'])

    def test_live_source_still_stops_on_stale_sensor_reset_or_joystick_button(self):
        for change in (dict(age=.31),dict(epoch=1),dict(pressed=True)):
            self.setUp()
            values,_ = self.bind_live_source()
            self.enable()
            values.update(change)
            self.now += .02
            self.cycle()
            if change.get('pressed'):
                for _ in range(16):
                    self.now += .05
                    self.cycle()
            self.assertFalse(self.worker.enabled,change)
            count = len(self.bus.writes)
            values.update(age=0.,epoch=0,pressed=False)
            self.cycle()
            self.assertEqual(len(self.bus.writes),count)

    def test_connect_cycle_and_shutdown_are_read_only_without_enable(self):
        for _ in range(3): self.cycle()
        self.assertEqual(self.bus.writes,[])
        self.bus.on_read = lambda register:self.worker.closed.set()
        self.worker.run()
        self.assertEqual(self.bus.writes,[])
        self.assertFalse(self.worker.snapshot()["connected"])

    def test_startup_retries_missing_motors_read_only_and_drops_old_enables(self):
        self.worker.request('enable_arm')
        with patch.object(self.arm,'connect',side_effect=[RuntimeError('Missing motor IDs: 1 2 3 4 5 6'),None]) as connect, \
             patch.object(self.arm,'close') as close,patch.object(self.worker.closed,'wait',return_value=False):
            self.assertTrue(self.worker.connect_arm(self.arm))
        self.assertEqual(connect.call_count,2)
        close.assert_called_once()
        self.assertTrue(self.worker.commands.empty())
        self.assertFalse(self.worker.enabled)
        self.assertEqual(self.bus.writes,[])

    def test_startup_retries_are_bounded_and_never_bypass_wrong_motor_models(self):
        for error,attempts in ((ConnectionError('No status packet'),3),
                               (RuntimeError('Motors with incorrect model numbers:'),1)):
            with patch.object(self.arm,'connect',side_effect=error) as connect,patch.object(self.arm,'close'), \
                 patch.object(self.worker.closed,'wait',return_value=False),self.assertRaises(type(error)):
                self.worker.connect_arm(self.arm)
            self.assertEqual(connect.call_count,attempts)
        with patch.object(self.arm,'connect',side_effect=ConnectionError('No response')),patch.object(self.arm,'close'), \
             patch.object(self.worker.closed,'wait',return_value=True):
            self.assertFalse(self.worker.connect_arm(self.arm))

    def test_reconnect_clears_motion_and_slider_reference_and_refuses_second_worker(self):
        self.worker.request('enable_arm')
        self.worker.slider.reference = object()
        self.worker.slider.enabled = self.worker.claw.enabled = self.worker.enabled = True
        with patch('robot_control.wearable.threading.Thread') as thread:
            thread.return_value.is_alive.return_value = True
            self.worker.reconnect()
            thread.return_value.start.assert_called_once()
            self.assertFalse(self.worker.enabled or self.worker.claw.enabled or self.worker.slider.enabled)
            self.assertIsNone(self.worker.slider.reference)
            self.assertTrue(self.worker.commands.empty())
            with self.assertRaises(ValueError): self.worker.reconnect()
        self.assertEqual(self.bus.writes,[])
        self.worker.closed.set()
        with self.assertRaises(ValueError): self.worker.reconnect()

    def test_live_tuning_is_atomic_bounded_and_rejects_unknown_settings(self):
        original = deepcopy(self.worker.config)
        self.worker.tune({'speed_deg_s':90})
        pending = deepcopy(self.worker.pending_config)
        bad = [{'speed_deg_s':v} for v in (True,float('nan'),float('inf'),0,361,'90')]
        bad += [{'response_ms':v} for v in (0,101)]
        bad += [{'control_hz':v} for v in (19,101)]
        bad += [{'joint_lead_deg':{n:v}} for n,v in [('shoulder_pan',0),('elbow_flex',46),('gripper',3)]]
        bad += [{'max_load':1024},{'gripper_max_load':0},{'load_stops_enabled':1},{'claw_speed_per_s':4},
                {'alignment_deg_s':120},{'speed_deg_s':100,'joint_lead_deg':{'elbow_flex':float('nan')}}]
        for changes in bad:
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.worker.tune(changes)
            self.assertEqual(self.worker.pending_config,pending)
            self.assertEqual(self.worker.tuning_requested,1)
            self.assertEqual(self.worker.config,original)

    def test_dashboard_controls_apply_atomically_without_enabling_and_reject_old_revision(self):
        self.assertFalse(self.worker.config['load_stops_enabled'])
        self.worker.tune(dict(speed_deg_s=240,claw_speed_per_s=1.5,max_load=720,gripper_max_load=450,
                              load_stops_enabled=True),revision=0)
        self.assertEqual(self.worker.claw.load_limit,350)
        with self.assertRaises(ValueError): self.worker.tune({'max_load':800},revision=0)
        self.cycle()
        state = self.worker.snapshot()
        self.assertEqual(state['controls']['max_load'],720)
        self.assertEqual(self.worker.claw.load_limit,450)
        self.assertEqual(self.worker.claw.speed,1.5)
        self.assertTrue(self.worker.claw.load_stops_enabled)
        self.assertEqual(state['tuning_applied'],1)
        self.assertFalse(self.worker.enabled)
        self.assertEqual(self.bus.writes,[])

    def test_load_stops_off_by_default_but_enabled_stops_report_joint_and_value(self):
        self.enable()
        self.bus.loads['elbow_flex'] = 800
        self.cycle()
        self.assertTrue(self.worker.enabled)
        self.worker.tune({'load_stops_enabled':True})
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn('elbow_flex 800 >= 650',self.worker.snapshot()['message'])
        self.worker.tune({'load_stops_enabled':False})
        self.cycle()
        self.assertFalse(self.worker.enabled)

    def test_claw_load_stops_use_live_switch_and_threshold(self):
        self.enable('enable_claw')
        self.bus.loads['gripper'] = 800
        self.cycle()
        self.assertTrue(self.worker.claw.enabled)
        self.worker.tune({'load_stops_enabled':True,'gripper_max_load':850})
        self.cycle()
        self.assertTrue(self.worker.claw.enabled)
        self.worker.tune({'gripper_max_load':450})
        self.cycle()
        self.assertFalse(self.worker.claw.enabled)
        self.assertIn('800 >= 450',self.worker.snapshot()['message'])

    def test_response_profiles_are_repeatable_bounded_and_ordered(self):
        config = load_config()
        previous = None
        for index in range(len(RESPONSE_PROFILES)):
            values = response_profile(index)
            applied = apply_values(config,values)
            self.assertEqual(tuning_values(applied),values)
            self.assertEqual(response_profile_index(values),index)
            self.assertEqual(applied['max_load'],config['max_load'])
            self.assertEqual(applied['alignment_deg_s'],config['alignment_deg_s'])
            if previous:
                self.assertGreater(values['speed_deg_s'],previous['speed_deg_s'])
                self.assertLess(values['response_ms'],previous['response_ms'])
                for n in BODY: self.assertGreater(values['joint_lead_deg'][n],previous['joint_lead_deg'][n])
            previous = values
        self.assertEqual(response_profile(4),self.worker.startup_tuning)
        changed = response_profile(2)
        changed['joint_lead_deg']['shoulder_pan'] = 7
        self.assertEqual(response_profile(2)['joint_lead_deg']['shoulder_pan'],12)
        self.assertIsNone(response_profile_index(changed))
        self.assertIsNone(response_profile_index({**response_profile(2),'control_hz':50}))
        for value in (-1,5,True,2.5,'Fast'):
            with self.assertRaises(ValueError): response_profile(value)

    def make_tuning_panel(self):
        import tkinter as tk
        from robot_control.tuning_ui import TuningPanel
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        panel = TuningPanel(root,self.worker,lambda:self.worker.pause('UI Stop'))
        panel.window.withdraw()
        self.addCleanup(panel.close)
        return panel

    def test_simple_controls_batch_all_settings_and_nudge_repeatably_without_enabling(self):
        panel = self.make_tuning_panel()
        self.assertEqual(self.worker.tuning_requested,0)  # Opening does not apply a default.
        panel.choose(4)
        panel.choose(0)
        self.assertEqual(self.worker.snapshot()['tuning_pending'],response_profile(0))
        self.cycle()
        panel.nudge(1)
        self.assertEqual(self.worker.snapshot()['tuning_pending'],response_profile(1))
        self.cycle()
        self.assertEqual(self.worker.snapshot()['tuning'],response_profile(1))
        self.assertFalse(self.worker.enabled)
        self.assertEqual(self.bus.writes,[])

    def test_preset_overrides_unsent_advanced_edits_and_reset_syncs_both_windows(self):
        panel = self.make_tuning_panel()
        panel.open_advanced()
        panel.advanced.window.withdraw()
        panel.advanced.slide('speed_deg_s','90')
        self.assertEqual(panel.advanced.pending_values()['speed_deg_s'],90)
        self.assertEqual(self.worker.tuning_requested,0)
        panel.choose(4)
        self.assertIsNone(panel.advanced.pending_values())
        self.assertEqual(self.worker.snapshot()['tuning_pending'],response_profile(4))
        self.assertEqual(panel.advanced.fields['speed_deg_s'][1].get(),'240')
        panel.advanced.flush()  # An old advanced callback cannot undo the preset.
        self.assertEqual(self.worker.tuning_requested,1)
        self.cycle()
        panel.apply(self.worker.startup_tuning)
        self.cycle()
        self.assertEqual(self.worker.snapshot()['tuning'],self.worker.startup_tuning)
        self.assertEqual(panel.advanced.fields['speed_deg_s'][1].get(),'240')
        self.assertEqual(self.bus.writes,[])

    def test_custom_advanced_settings_are_preserved_when_simple_window_reopens(self):
        panel = self.make_tuning_panel()
        panel.open_advanced()
        panel.advanced.window.withdraw()
        entry = panel.advanced.fields['speed_deg_s'][1]
        entry.set('nan')
        panel.advanced.commit('speed_deg_s')
        self.assertEqual(self.worker.tuning_requested,0)
        entry.set('90')
        panel.advanced.commit('speed_deg_s')
        panel.close()  # Flushes the last valid edit, without resetting it.
        self.cycle()
        reopened = self.make_tuning_panel()
        self.assertEqual(reopened.level.get(),-1)
        self.assertIn('Custom',reopened.status.get())
        self.assertEqual(self.worker.config['speed_deg_s'],90)
        self.assertEqual(self.worker.tuning_requested,1)
        self.assertEqual(self.bus.writes,[])

    def test_tuning_coalesces_and_off_controller_stays_read_only(self):
        self.worker.tune({'speed_deg_s':80,'joint_lead_deg':{'shoulder_pan':4}})
        self.worker.tune({'speed_deg_s':90,'response_ms':20,'control_hz':100,'joint_lead_deg':{'elbow_flex':6}})
        self.assertEqual(self.worker.snapshot()['tuning_pending']['speed_deg_s'],90)
        self.assertEqual(self.worker.config['speed_deg_s'],240)
        self.cycle()
        state = self.worker.snapshot()
        self.assertEqual(state['tuning_applied'],2)
        self.assertIsNone(state['tuning_pending'])
        self.assertEqual(state['tuning']['joint_lead_deg']['shoulder_pan'],4)
        self.assertEqual(state['tuning']['joint_lead_deg']['elbow_flex'],6)
        self.assertEqual(state['tuning']['control_hz'],100)
        self.assertFalse(self.worker.enabled)
        self.assertFalse(self.worker.claw.enabled)
        self.assertEqual(self.bus.writes,[])

    def test_tuning_received_during_hardware_read_waits_until_next_cycle(self):
        def during_read(register):
            self.bus.on_read = lambda register:None
            self.worker.tune({'speed_deg_s':90,'response_ms':20})
        self.bus.on_read = during_read
        self.cycle()
        self.assertEqual(self.worker.config['speed_deg_s'],240)
        self.assertEqual(self.worker.snapshot()['tuning_applied'],0)
        self.assertEqual(self.worker.snapshot()['tuning_requested'],1)
        self.cycle()
        self.assertEqual(self.worker.config['speed_deg_s'],90)
        self.assertEqual(self.worker.snapshot()['tuning_applied'],1)

    def test_live_speed_changes_without_reenabling_or_resetting_anchor(self):
        self.worker.config['mapping_mode'] = 'relative'
        self.enable()
        anchor,source_anchor = dict(self.worker.anchor),dict(self.worker.source_anchor)
        self.worker.tune({'speed_deg_s':90,'response_ms':20,'joint_lead_deg':dict.fromkeys(BODY,8)})
        source = {n:source_anchor[n]+1 for n in BODY}
        with patch.object(self.worker,'source_target',return_value=source): self.cycle()
        expected_step = 90*4095/360*.05
        for n in BODY: self.assertAlmostEqual(self.worker.previous[n]-anchor[n],expected_step)
        self.assertEqual(self.worker.anchor,anchor)
        self.assertEqual(self.worker.source_anchor,source_anchor)
        self.assertEqual(sum(register=='Torque_Enable' for register,_ in self.bus.writes),1)
        self.assertTrue(self.worker.enabled)

    def test_lower_lead_accepts_previous_bound_once_then_enforces_new_bound(self):
        self.worker.config['mapping_mode'] = 'relative'
        self.worker.tune({'joint_lead_deg':dict.fromkeys(BODY,8)})
        self.enable()
        ticks = 4095/360
        self.worker.previous = {n:self.bus.positions[n]+8*ticks for n in BODY}
        self.worker.tune({'joint_lead_deg':dict.fromkeys(BODY,1)})
        self.cycle()
        self.assertTrue(self.worker.enabled)
        for n in BODY: self.assertLessEqual(abs(self.worker.previous[n]-self.bus.positions[n]),ticks+1e-8)
        self.worker.previous['shoulder_pan'] = self.bus.positions['shoulder_pan']+3*ticks
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn('tracking error',self.worker.snapshot()['message'])

    def test_raising_lead_does_not_hide_existing_tracking_fault(self):
        self.worker.config['mapping_mode'] = 'relative'
        self.worker.tune({'joint_lead_deg':dict.fromkeys(BODY,3)})
        self.enable()
        self.worker.previous['shoulder_pan'] = self.bus.positions['shoulder_pan']+7*4095/360
        self.worker.tune({'joint_lead_deg':{'shoulder_pan':8}})
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn('tracking error',self.worker.snapshot()['message'])

    def test_live_tuning_preserves_alignment_rate_and_captured_pose(self):
        self.enable()
        self.assertTrue(self.worker.aligning)
        captured = dict(self.worker.alignment_target)
        previous = dict(self.worker.previous)
        self.worker.tune({'speed_deg_s':120,'response_ms':5,'joint_lead_deg':dict.fromkeys(BODY,8)})
        self.cycle()
        self.assertTrue(self.worker.aligning)
        self.assertEqual(self.worker.alignment_target,captured)
        for n in BODY: self.assertLessEqual(abs(self.worker.previous[n]-previous[n]),self.worker.config['alignment_deg_s']*4095/360*.05+1e-8)
        self.assertEqual(self.worker.config['max_load'],650)

    def test_tuning_does_not_override_stop_and_reset_restores_startup_values(self):
        self.worker.request('enable_arm')
        self.worker.tune({'speed_deg_s':90,'response_ms':20,'control_hz':100,'joint_lead_deg':dict.fromkeys(BODY,8)})
        self.worker.pause('test stop')
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertEqual(self.bus.writes,[])
        self.worker.tune(self.worker.startup_tuning)
        self.cycle()
        self.assertEqual(self.worker.snapshot()['tuning'],self.worker.startup_tuning)
        self.assertIn('PAUSED: test stop',self.worker.snapshot()['message'])
        self.assertEqual(self.bus.writes,[])

    def test_smoothing_tuning_changes_small_step_response_without_overshoot(self):
        measured = dict.fromkeys(BODY,2000.)
        desired = dict.fromkeys(BODY,2005.)
        limits = dict.fromkeys(BODY,(0,4095))
        config = load_config()
        config['response_ms'] = 35
        smooth = bounded_targets(desired,measured,measured,limits,.02,config)
        config['response_ms'] = 5
        fast = bounded_targets(desired,measured,measured,limits,.02,config)
        for n in BODY:
            self.assertLess(smooth[n],fast[n])
            self.assertLess(fast[n],desired[n])

    def test_enable_latches_current_pose_without_jumping_to_urdf_neutral(self):
        self.publish(geometry(.7,1.,.6,.3))
        measured = {n:self.bus.positions[n] for n in BODY}
        self.enable()
        self.assertTrue(self.worker.enabled)
        self.assertEqual(self.bus.writes,[("Goal_Position",measured),("Torque_Enable",dict.fromkeys(BODY,1))])
        self.assertFalse(self.worker.claw.enabled)

    def test_raised_arm_uses_same_mapping_and_bounded_raw_step(self):
        self.worker.config['mapping_mode'] = 'relative'
        self.enable()
        anchor = dict(self.worker.anchor)
        self.now += .05
        self.publish(geometry(.4,.4,.4,heading=.4))
        self.cycle()
        target = self.bus.writes[-1][1]
        self.assertLess(target["shoulder_lift"],anchor["shoulder_lift"])
        self.assertLess(target["shoulder_pan"],anchor["shoulder_pan"])
        bound = math.ceil(self.worker.config["speed_deg_s"]*4095/360*.05)
        self.assertTrue(all(abs(target[n]-anchor[n])<=bound for n in BODY))

    def test_absolute_mapping_uses_saved_ranges_offsets_and_full_base_shoulder_travel(self):
        mapping = self.worker.joint_mapping
        limits = {n:self.arm.limits(n) for n in BODY}
        direction = self.worker.config['raw_direction']
        for fraction in (0.,.25,.5,.75,1.):
            source = {}
            for n in BODY:
                low,high = mapping.urdf_limits[n]
                source[n] = low+fraction*(high-low)*mapping.config['urdf_scales'][n]+math.radians(mapping.config['urdf_offsets_deg'][n])
            targets = mapping.raw_targets(source,limits,direction)
            for n in BODY:
                if n=='wrist_roll': continue  # Roll uses encoder degrees; tested separately below.
                low,high = limits[n]
                expected = low+(1-fraction if mapping.config['joint_flips'][n] else fraction)*(high-low)+mapping.trim_deg[n]*TICKS_PER_DEGREE
                self.assertAlmostEqual(targets[n],expected)

    def test_folded_start_aligns_to_urdf_without_preserving_old_offset(self):
        self.bus.positions.update(wrist_flex=606,elbow_flex=3486,shoulder_lift=2722)
        self.cycle()
        requested = dict(self.worker.snapshot()['desired'])
        self.assertEqual(self.bus.writes,[])  # Preview targets are read-only.
        self.assertGreater(requested['wrist_flex'],1500)
        self.assertLess(requested['elbow_flex'],2000)
        start = dict(self.bus.positions)
        self.enable()
        self.assertTrue(self.worker.aligning)
        self.assertEqual(self.bus.writes[0],('Goal_Position',{n:start[n] for n in BODY}))
        for _ in range(360):
            previous = dict(self.bus.positions)
            was_aligning = self.worker.aligning
            self.now += .05
            self.publish()
            self.cycle()
            goals = self.bus.writes[-1][1]
            speed = self.worker.config['alignment_deg_s'] if was_aligning and self.worker.aligning else self.worker.config['speed_deg_s']
            self.assertTrue(all(abs(goals[n]-previous[n])<=math.ceil(speed*4095/360*.05)+1 for n in BODY))
            self.bus.positions.update(goals)
        self.assertFalse(self.worker.aligning)
        self.assertTrue(self.worker.enabled)
        self.assertEqual(self.worker.snapshot()['limited'],[])
        for n in BODY: self.assertAlmostEqual(self.bus.positions[n],requested[n],delta=1)

    def test_alignment_target_is_frozen_and_stop_requires_new_enable(self):
        self.enable()
        first = dict(self.worker.alignment_target)
        self.publish(geometry(.6,.8,.4,heading=.8))
        self.cycle()
        self.assertEqual(self.worker.snapshot()['desired'],first)
        self.worker.pause('operator stop')
        self.cycle()
        self.assertFalse(self.worker.aligning)
        self.assertFalse(self.worker.enabled)
        count = len(self.bus.writes)
        self.publish()
        self.cycle()
        self.assertEqual(len(self.bus.writes),count)

    def test_screenshot_errors_complete_alignment_then_follow_new_input(self):
        self.enable()
        # Actual 21:36:57 screenshot: shoulder 3.4° and elbow 2.5° short.
        requested = dict(zip(BODY,(1308,3026,1672,1662,1632)))
        actual = dict(zip(BODY,(1306,3065,1701,1662,1627)))
        self.bus.positions.update(actual)
        self.worker.previous = dict(zip(BODY,(1308,3031,1672,1662,1632)))
        self.worker.alignment_target = requested
        self.worker.alignment = Alignment(requested,actual,self.worker.limits,self.now,self.worker.config)
        for _ in range(12):
            self.now += .05
            self.publish()
            self.cycle()
        self.assertTrue(self.worker.enabled)
        self.assertFalse(self.worker.aligning)
        self.assertIn('LIVE calibrated',self.worker.snapshot()['message'])
        self.publish(geometry(heading=.5))
        self.now += .02
        self.cycle()
        self.assertNotEqual(self.worker.snapshot()['desired']['shoulder_pan'],requested['shoulder_pan'])

    def test_alignment_stall_holds_with_joint_reason_and_never_auto_resumes(self):
        self.cycle()
        requested = self.worker.snapshot()['desired']
        self.bus.positions.update({n:round(v) for n,v in requested.items()})
        self.bus.positions['shoulder_lift'] -= 15*4095/360
        self.enable()
        for _ in range(140):
            self.now += .05
            self.publish()
            self.cycle()
        state = self.worker.snapshot()
        self.assertFalse(state['enabled'])
        self.assertFalse(state['aligning'])
        self.assertIn('Alignment stalled: shoulder_lift',state['message'])
        self.assertEqual(self.bus.writes[-1],('Goal_Position',{n:round(self.bus.positions[n]) for n in BODY}))
        count = len(self.bus.writes)
        self.publish()
        self.cycle()
        self.assertEqual(len(self.bus.writes),count)

    def test_alignment_requires_settling_and_has_deadline_even_with_slow_progress(self):
        target = dict.fromkeys(BODY,2000.)
        limits = dict.fromkeys(BODY,(0,4095))
        config = load_config()
        monitor = Alignment(target,target,limits,0,config)
        self.assertFalse(monitor.update(target,0))
        self.assertFalse(monitor.update(target,.2))
        outside = {**target,'shoulder_lift':2100}
        self.assertFalse(monitor.update(outside,.3))
        self.assertFalse(monitor.update(target,.4))
        self.assertFalse(monitor.update(target,.7))
        self.assertTrue(monitor.update(target,.81))
        monitor = Alignment(target,dict.fromkeys(BODY,3500.),limits,0,config)
        for second in range(30):
            self.assertFalse(monitor.update(dict.fromkeys(BODY,3500.-second*12),second))
        with self.assertRaisesRegex(ValueError,'timed out'):
            monitor.update(dict.fromkeys(BODY,3140.),30)

    def test_limiter_distinguishes_large_request_from_motor_lead_and_range(self):
        measured = dict.fromkeys(BODY,2000.)
        desired = dict.fromkeys(BODY,3000.)
        limits = dict.fromkeys(BODY,(100,3500))
        previous = dict(measured)
        for _ in range(60):
            flags = {}
            previous = bounded_targets(desired,previous,measured,limits,.02,load_config(),flags=flags)
        self.assertIn('LEAD',flags['shoulder_pan'])
        for name in BODY:
            self.assertAlmostEqual(previous[name]-2000,lead_degrees(load_config(),name)*TICKS_PER_DEGREE)
        self.assertGreater(desired['shoulder_pan']-previous['shoulder_pan'],600)
        desired['shoulder_lift'] = 5000
        limited = []
        bounded_targets(desired,previous,measured,limits,.02,load_config(),limited,flags)
        self.assertEqual(limited,['shoulder_lift'])
        self.assertIn('RANGE',flags['shoulder_lift'])

    def test_gap_reset_button_and_direction_change_require_manual_reenable(self):
        for change in (dict(age=.31),dict(valid=False),dict(epoch=(1,)),dict(pressed=True)):
            with self.subTest(change=change):
                self.setUp()
                self.enable()
                self.publish(**change)
                self.cycle()
                if change.get('pressed'):
                    for _ in range(16):
                        self.now += .05
                        self.publish(**change)
                        self.cycle()
                self.assertFalse(self.worker.enabled)
                self.assertEqual(self.bus.writes[-1],("Goal_Position",{n:self.bus.positions[n] for n in BODY}))
                count = len(self.bus.writes)
                self.publish()
                self.cycle()
                self.assertEqual(len(self.bus.writes),count)

    def test_stopped_ui_heartbeat_holds_even_with_cached_healthy_imus(self):
        self.enable()
        self.now += .4
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn("stale",self.worker.snapshot()["message"])
        self.assertIn('wearable UI update',self.worker.snapshot()['message'])

    def test_slow_robot_reads_cannot_use_old_measurements_even_if_input_keeps_up(self):
        self.enable()
        def delay(register):
            self.now += .1
            self.publish()
        self.bus.on_read = delay
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn("telemetry too slow",self.worker.snapshot()["message"])

    def test_web_enable_reports_success_refusal_cancel_and_already_enabled(self):
        from robot_control import joints
        self.assertEqual((joints.JOINTS, joints.BODY), (JOINTS, BODY))
        reply = lambda: self.worker.snapshot()['enable_request']
        self.assertIsNone(reply())
        self.worker.request('enable_arm', request_id='cancelled')
        self.worker.pause('operator Stop')
        self.cycle()
        self.assertEqual((reply()['id'], reply()['ok']), ('cancelled', False))
        self.assertIn('Stop', reply()['detail'])
        self.bus.positions['shoulder_lift'] = 4000  # Outside the saved range: the worker refuses.
        self.worker.request('enable_arm', request_id='refused')
        self.cycle()
        self.assertEqual((reply()['id'], reply()['ok']), ('refused', False))
        self.assertIn('Enable refused', reply()['detail'])
        self.setUp()
        self.worker.request('enable_arm', request_id='first')
        self.cycle()
        self.assertTrue(self.worker.enabled)
        self.assertEqual((reply()['id'], reply()['ok']), ('first', True))
        self.worker.request('enable_arm', request_id='again')
        self.cycle()
        self.assertEqual((reply()['id'], reply()['ok'], reply()['detail']), ('again', True, 'Arm already enabled.'))
        self.worker.request('enable_claw', request_id='claw')
        self.cycle()
        self.assertEqual((reply()['id'], reply()['ok']), ('claw', True))
        self.worker.request('enable_arm')  # Desktop/slider requests carry no id and never overwrite the reply.
        self.cycle()
        self.assertEqual(reply()['id'], 'claw')

    def test_snapshot_carries_identity_and_startup_tuning_for_the_web_console(self):
        state = self.worker.snapshot()
        self.assertEqual((state['port'], state['mapping_mode']), ('TEST', self.worker.config['mapping_mode']))
        self.assertEqual(state['alignment_deg_s'], self.worker.config['alignment_deg_s'])
        self.assertEqual(state['startup_tuning'], self.worker.startup_tuning)
        state['startup_tuning']['speed_deg_s'] = 1  # A copy, not the worker's own dict.
        self.assertNotEqual(self.worker.startup_tuning['speed_deg_s'], 1)

    def test_pause_cancels_queued_enable(self):
        self.worker.request("enable_arm")
        self.worker.pause()
        self.cycle()
        self.assertEqual(self.bus.writes,[])
        self.assertFalse(self.worker.enabled)

    def test_pause_or_stale_during_hardware_validation_cannot_enable(self):
        for interrupted in (True,False):
            self.setUp()
            def on_read(register):
                if register=="Homing_Offset":
                    if interrupted: self.worker.pause()
                    else: self.now += .4
            self.bus.on_read = on_read
            self.enable()
            self.assertEqual(self.bus.writes,[])

    def test_mismatched_calibration_is_refused_without_writes(self):
        self.bus.homing_error = True
        self.enable()
        self.assertEqual(self.bus.writes,[])
        self.assertIn("does not match",self.worker.snapshot()["message"])

    def test_nonfinite_or_out_of_range_position_is_not_commanded(self):
        self.bus.positions["shoulder_lift"] = 4000
        self.enable()
        self.assertEqual(self.bus.writes,[])
        self.bus.positions["shoulder_lift"] = math.nan
        with self.assertRaises(ValueError): self.cycle()

    def test_excessive_load_or_torque_loss_stops_body(self):
        for fault in ("load","torque"):
            self.setUp()
            self.worker.tune({'load_stops_enabled':True})
            self.enable()
            if fault=="load": self.bus.loads["elbow_flex"] = 650
            else: self.bus.torque["shoulder_pan"] = 0
            self.cycle()
            self.assertFalse(self.worker.enabled)
            self.assertFalse(any(r=="Torque_Enable" and n.get("shoulder_pan")==1 for r,n in self.bus.writes[2:]))
            if fault=="torque":
                self.assertFalse(any("shoulder_pan" in values for _,values in self.bus.writes[2:]))

    def test_claw_is_separate_requires_center_and_respects_position_endpoints(self):
        self.publish(joy_y=0)
        self.enable("enable_claw")
        self.assertEqual(self.bus.writes,[])
        self.publish()
        start = self.bus.positions["gripper"]
        self.enable("enable_claw")
        self.assertEqual(self.bus.writes,[("Goal_Position",{"gripper":start}),("Torque_Enable",{"gripper":1})])
        self.assertFalse(self.worker.enabled)
        self.publish(joy_y=0)
        self.cycle()
        self.assertGreater(self.bus.writes[-1][1]["gripper"],start)
        self.publish(pressed=True)
        self.cycle()
        for _ in range(16):
            self.now += .05
            self.publish(pressed=True)
            self.cycle()
        self.assertFalse(self.worker.claw.enabled)

    def test_claw_reverse_matches_preview_setting(self):
        reverse = dict.fromkeys(JOINTS,False)
        reverse["gripper"] = True
        self.publish(reverse=reverse)
        self.enable("enable_claw")
        self.publish(joy_y=0,reverse=reverse)
        self.cycle()
        self.assertLess(self.bus.writes[-1][1]["gripper"],self.bus.positions["gripper"])

    def test_hardware_failure_does_not_reconnect_or_enable_again(self):
        self.enable()
        def fail(register): raise ConnectionError("USB unplugged")
        self.bus.on_read = fail
        count = len(self.bus.writes)
        with self.assertLogs("wearable",level="INFO"):
            self.worker.run()
        self.assertFalse(self.worker.snapshot()["connected"])
        self.assertEqual(len(self.bus.writes),count)

    def test_joint_lead_limits_and_finite_checks(self):
        values = dict.fromkeys(BODY,2000.)
        limits = dict.fromkeys(BODY,(100,3500))
        source = dict.fromkeys(BODY,2.)
        previous = dict(values)
        for _ in range(200):
            previous = bounded_follow(values,dict.fromkeys(BODY,0),source,previous,values,limits,.05,load_config())
        self.assertLessEqual(previous['shoulder_pan']-2000,lead_degrees(load_config(),'shoulder_pan')*TICKS_PER_DEGREE+1e-9)
        limits["shoulder_pan"] = (100,2001)
        out = bounded_follow(values,dict.fromkeys(BODY,0),source,values,values,limits,.1,load_config())
        self.assertGreater(out["shoulder_pan"],2000)
        self.assertLessEqual(out["shoulder_pan"],2001)
        source["shoulder_pan"] = math.nan
        with self.assertRaises(ValueError): bounded_follow(values,values,source,values,values,limits,.05,load_config())

    def test_normal_arm_motion_tracks_without_old_ten_degree_per_second_lag(self):
        # Ideal servo follows each command: isolate host lag from hardware issues.
        def follow(config,dt):
            anchor = dict.fromkeys(BODY,2000.)
            zeros = dict.fromkeys(BODY,0.)
            limits = dict.fromkeys(BODY,(100,3500))
            previous = dict(anchor)
            t = 0.
            while t<1.-1e-8:
                t = min(1.,t+dt)
                source = dict.fromkeys(BODY,math.radians(25*t))
                result = bounded_follow(anchor,zeros,source,previous,previous,limits,dt,config)
                self.assertLessEqual(abs(result['shoulder_pan']-previous['shoulder_pan']),
                                     config['speed_deg_s']*4095/360*dt+1e-8)
                previous = result
            return (previous['shoulder_pan']-2000)*360/4095
        config = load_config()
        old = follow({**config,'speed_deg_s':10},.0625)
        new = follow(config,.02)
        self.assertLessEqual(old,10)
        self.assertGreater(new,23)
        self.assertLess(new,25)

    def test_small_sensor_jitter_is_not_amplified_and_range_limits_are_reported(self):
        config = load_config()
        anchor = dict.fromkeys(BODY,2000.)
        zero = dict.fromkeys(BODY,0.)
        limits = dict.fromkeys(BODY,(100,3500))
        previous = dict(anchor)
        for index in range(60):
            source = dict.fromkeys(BODY,math.radians(.25 if index%2 else -.25))
            previous = bounded_follow(anchor,zero,source,previous,previous,limits,.02,config)
            self.assertLess(abs(previous['shoulder_pan']-2000)*360/4095,.25)
        limited = []
        limits['shoulder_pan'] = (1990,2010)
        out = bounded_follow(anchor,zero,dict.fromkeys(BODY,1.),anchor,anchor,limits,.02,config,limited)
        self.assertEqual(limited,['shoulder_pan'])
        self.assertLessEqual(out['shoulder_pan'],2010)

    def test_timing_and_blocked_input_are_visible_to_operator(self):
        self.publish(valid=False)
        self.worker.cycle(self.arm,.02)
        state = self.worker.snapshot()
        self.assertAlmostEqual(state['loop_hz'],50)
        self.assertIn('recenter',state['input_status'])
        self.assertFalse(state['can_enable'])

    def test_loop_uses_precise_durations_despite_coarse_windows_packet_clock(self):
        self.worker.clock = lambda:math.floor(self.now/.015625)*.015625
        reads = []
        def read(register):
            self.now += .001
            reads.append(register)
            if len(reads)>=24: self.worker.closed.set()
        self.bus.on_read = read
        def sleep(seconds):
            self.assertGreater(seconds,0)
            self.assertLessEqual(seconds,.02)
            self.now += seconds
        with patch('robot_control.wearable.time.sleep',side_effect=sleep):
            self.worker.run()
        state = self.worker.snapshot()
        self.assertAlmostEqual(state['loop_hz'],100,places=5)
        self.assertAlmostEqual(state['read_ms'],3,places=5)
        self.assertEqual(self.bus.writes,[])

    def test_input_packet_carries_sensor_and_reference_and_direction_versions(self):
        hub = DemoHub()
        snapshot = hub.snapshot()
        now = snapshot["wroom"]["joystick"]["seen"]
        first = input_packet(ArmModel().geometry(),snapshot,True,"",1,{},now)
        for revision,reverse in ((2,{}),(1,{"shoulder_pan":True}),(1,{"gripper":True})):
            other = input_packet(ArmModel().geometry(),snapshot,True,"",revision,reverse,now)
            self.assertNotEqual(first["epoch"],other["epoch"])
        snapshot["s3"]["epoch"] += 1
        other = input_packet(ArmModel().geometry(),snapshot,True,"",1,{},now)
        self.assertNotEqual(first["epoch"],other["epoch"])


class ResponsiveMatchingTests(unittest.TestCase):
    def setUp(self):
        self.arm = Arm('TEST')
        self.worker = WearableRobot('TEST',load_calibration())
        self.limits = {n:self.arm.limits(n) for n in JOINTS}

    def test_physical_target_matches_working_preview_for_representative_poses(self):
        from wearable_test.robot_preview import RobotMapping
        preview = RobotMapping()
        reverse = dict.fromkeys(JOINTS,False)
        poses = [geometry(), geometry(.3,.8,.6,heading=.4,roll=.5),
                 geometry(-math.pi/2,0,0,forearm_heading=.6), geometry(-.3,-.2,.1,heading=-.5,roll=-.6)]
        for index,pose in enumerate(poses):
            packet = dict(epoch=(1,),geometry=pose,reverse=reverse)
            source = self.worker.source_target(packet)
            expected,_ = preview.targets(pose,None,0)
            raw = self.worker.joint_mapping.raw_targets(source,self.limits,self.worker.config['raw_direction'])
            roundtrip = self.worker.joint_mapping.urdf_angles(raw,self.limits,self.worker.config['raw_direction'])
            for name in BODY:
                self.assertAlmostEqual(source[name],expected[name],places=7,msg=f'{index}: {name}')
                self.assertAlmostEqual(roundtrip[name],expected[name],places=7)

    def test_twenty_degree_preview_changes_keep_their_scale_and_direction(self):
        mapping = self.worker.joint_mapping
        direction = self.worker.config['raw_direction']
        neutral = {n:sum(mapping.urdf_limits[n])/2 for n in BODY}
        raw_zero = mapping.raw_targets(neutral,self.limits,direction)
        gains = mapping.degree_gains(self.limits,direction)
        for name in BODY:
            for degrees in (-20,20):
                target = {**neutral,name:neutral[name]+math.radians(degrees)}
                raw = mapping.raw_targets(target,self.limits,direction)
                movement = (raw[name]-raw_zero[name])/TICKS_PER_DEGREE
                # Existing mechanical registration is nearly unity, never a 20:1 reduction.
                self.assertGreater(abs(movement),18)
                self.assertLess(abs(movement),24)
                self.assertAlmostEqual(movement,degrees*gains[name])
                restored = mapping.urdf_angles(raw,self.limits,direction)
                self.assertAlmostEqual(math.degrees(restored[name]-neutral[name]),degrees)
                for other in BODY:
                    if other!=name: self.assertEqual(raw[other],raw_zero[other])

    def test_twenty_degree_steps_are_requested_in_full_within_120ms(self):
        config = self.worker.config
        center = dict.fromkeys(BODY,2000.)
        limits = dict.fromkeys(BODY,(0,4095))
        for sign in (-1,1):
            desired = {n:2000+sign*20*TICKS_PER_DEGREE for n in BODY}
            previous = dict(center)
            # Even if the encoder hasn't moved yet, the old 3/5 degree clip
            # must not shrink a 20 degree request. No hardware response claimed.
            for _ in range(12):
                previous = bounded_targets(desired,previous,center,limits,.01,config)
            for name in BODY:
                self.assertLess(abs(previous[name]-desired[name])/TICKS_PER_DEGREE,.1)

    def test_following_servo_with_loaded_joint_bias_tracks_fast_movement(self):
        config = self.worker.config
        previous = measured = dict.fromkeys(BODY,2000.)
        limits = dict.fromkeys(BODY,(0,4095))
        for index in range(1,101):
            # 60 deg/s triangular hand motion, 60 ms motor response, and 4 deg
            # load-dependent bias (similar to the recorded shoulder residual).
            degrees = min(index,100-index)*.6
            desired = dict.fromkeys(BODY,2000+degrees*TICKS_PER_DEGREE)
            previous = bounded_targets(desired,previous,measured,limits,.01,config)
            measured = {n:measured[n]+(1-math.exp(-.01/.06))*(previous[n]-4*TICKS_PER_DEGREE-measured[n]) for n in BODY}
            self.assertLess(abs(previous['shoulder_lift']-desired['shoulder_lift'])/TICKS_PER_DEGREE,.2)
        self.assertLess(abs(measured['shoulder_lift']-2000)/TICKS_PER_DEGREE,5)

    def test_wrist_trim_is_exactly_45_physical_degrees_not_scaled_urdf_degrees(self):
        trimmed = self.worker.joint_mapping
        original = JointMapping(trimmed.urdf_limits)
        source = dict.fromkeys(BODY,0.)
        direction = self.worker.config['raw_direction']
        before = original.raw_targets(source,self.limits,direction)
        after = trimmed.raw_targets(source,self.limits,direction)
        for name in BODY:
            self.assertAlmostEqual((after[name]-before[name])/TICKS_PER_DEGREE,45 if name=='wrist_roll' else 0)
        self.assertAlmostEqual(trimmed.urdf_angles(after,self.limits,direction)['wrist_roll'],0)
        for invalid in ({'gripper':20},{'wrist_roll':float('nan')},{'wrist_roll':True},{'wrist_roll':46}):
            with self.assertRaises(ValueError): JointMapping(trimmed.urdf_limits,trim_deg=invalid)

    def test_hand_roll_drives_exactly_double_physical_wrist_angle(self):
        mapping = self.worker.joint_mapping
        reverse = dict.fromkeys(JOINTS,False)
        def raw(degrees):
            source = self.worker.source_target(dict(epoch=(1,),geometry=geometry(roll=math.radians(degrees)),reverse=reverse))
            return mapping.raw_targets(source,self.limits,self.worker.config['raw_direction'])['wrist_roll']
        center = raw(0)
        for degrees in (-30,-1,1,30):
            self.assertAlmostEqual((raw(degrees)-center)/TICKS_PER_DEGREE,-2*degrees,places=6)

    def test_learned_human_ranges_reach_both_physical_flex_limits(self):
        from wearable_test.robot_preview import RobotMapping
        demo,model = DemoHub(),ArmModel()
        model.calibrate(demo.frame('A'),demo.frame('B'))
        model.capture_motion_range('C',demo.frame('C'))
        model.capture_motion_range('D',demo.frame('D'))
        preview = RobotMapping()
        def targets(frame):
            g = model.geometry(frame)
            result = self.worker.source_target(dict(epoch=(1,),geometry=g,reverse=dict.fromkeys(JOINTS,False)))
            shown,_ = preview.targets(g,None,0)
            for name in BODY: self.assertAlmostEqual(result[name],shown[name])
            return self.worker.joint_mapping.raw_targets(result,self.limits,self.worker.config['raw_direction'])
        self.assertAlmostEqual(targets(demo.frame('A'))['elbow_flex'],self.limits['elbow_flex'][0])
        self.assertAlmostEqual(targets(demo.frame('C'))['elbow_flex'],self.limits['elbow_flex'][1])
        self.assertAlmostEqual(targets(demo.frame('B'))['shoulder_lift'],self.limits['shoulder_lift'][1])
        self.assertAlmostEqual(targets(demo.frame('D'))['shoulder_lift'],self.limits['shoulder_lift'][0])

    def test_learned_pan_reaches_physical_endpoints_and_matches_preview(self):
        from wearable_test.robot_preview import RobotMapping
        demo,model = DemoHub(),ArmModel()
        model.calibrate(demo.frame('A'),demo.frame('B'))
        model.capture_motion_range('E',demo.frame('E'))
        model.capture_motion_range('F',demo.frame('F'))
        preview = RobotMapping()
        positions = []
        for pose in ('E','A','F'):
            g = model.geometry(demo.frame(pose))
            result = self.worker.source_target(dict(epoch=(1,),geometry=g,reverse=dict.fromkeys(JOINTS,False)))
            shown,_ = preview.targets(g,None,0)
            self.assertAlmostEqual(result['shoulder_pan'],shown['shoulder_pan'])
            positions.append(self.worker.joint_mapping.raw_targets(result,self.limits,self.worker.config['raw_direction'])['shoulder_pan'])
        low,high = self.limits['shoulder_pan']
        self.assertAlmostEqual(min(positions),low)
        self.assertAlmostEqual(max(positions),high)
        self.assertAlmostEqual(positions[1],(low+high)/2)

    def test_fast_claw_reaches_both_endpoints_in_half_a_second_and_retains_stops(self):
        claw = self.worker.claw
        low,high = self.limits['gripper']
        sample = dict(sequence=1,seen=0,epoch=0,x=1360,y=1810,pressed=False)
        claw.enable(sample,low,(low,high),0)
        measured = low
        for raw,expected in ((0,high),(4095,low)):
            for index in range(1,51):
                now = index*.01
                sample.update(seen=now,y=raw)
                target = claw.step(sample,measured,0,.01,now)
                self.assertLessEqual(abs(target-measured),claw.speed*(high-low)*.01+1e-8)
                measured = target
            self.assertAlmostEqual(measured,expected)
        claw.load_stops_enabled = True
        for changes,load in ((dict(pressed=True),0),(dict(seen=-1),0),(dict(epoch=1),0),({},350)):
            with self.assertRaises(ValueError): claw.step({**sample,**changes},measured,load,.01,sample['seen'])


class TelemetryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.arm = Arm('TEST')
        self.arm.bus = FakeBus(self.arm.calibration)
        self.worker = WearableRobot('TEST',load_calibration())
        self.limits = {n:self.arm.limits(n) for n in JOINTS}

    def test_only_fresh_imu_samples_are_published_with_original_age(self):
        frames = []
        publisher = WearableTelemetry.__new__(WearableTelemetry)
        publisher.socket = SimpleNamespace(send_string=lambda frame,flags:frames.append(frame))
        publisher.next_sample = 0
        publisher.last_error = None
        snapshot = {'wroom':{'samples':{'wroom':{'q':[1,0,0,0],'seen':9.9}}}}
        args = dict(positions=self.arm.bus.positions,loads=self.arm.bus.loads,targets={},limits=self.limits,
                    mapping=self.worker.joint_mapping,direction=self.worker.config['raw_direction'],
                    enabled=False,input_fresh=False,sampled_at=100,now=10,wearable_snapshot=snapshot,sensor_now=10)
        publisher.publish(**args)
        self.assertEqual([f.split(' ',1)[0] for f in frames],['robot','imu'])
        imu = json.loads(frames[-1].split(' ',1)[1])
        self.assertAlmostEqual(imu['t'],99.9)
        self.assertEqual(imu['q'],[1,0,0,0])
        frames.clear()
        publisher.publish(**{**args,'now':11,'sensor_now':11})
        self.assertEqual([f.split(' ',1)[0] for f in frames],['robot'])

    def test_measurement_mapping_roundtrips_absolute_targets(self):
        mapping = self.worker.joint_mapping
        for flipped in (False,True):
            for sign in (1,-1):
                direction = dict.fromkeys(BODY,sign)
                mapping.config['joint_flips'] = dict.fromkeys(JOINTS,flipped)
                angles = {n:(low+high)*.5 for n,(low,high) in mapping.urdf_limits.items() if n in BODY}
                raw = mapping.raw_targets(angles,self.limits,direction)
                raw['gripper'] = sum(self.limits['gripper'])/2
                pose = console_positions(raw,self.limits,mapping,direction)
                for name in BODY:
                    self.assertAlmostEqual(pose[name],math.degrees(angles[name]))
                self.assertEqual(pose['gripper'],50)

    def test_live_publisher_reuses_bus_sample_and_never_writes(self):
        publisher = WearableTelemetry('tcp://127.0.0.1:0')
        context = zmq.Context()
        subscriber = context.socket(zmq.SUB)
        subscriber.setsockopt_string(zmq.SUBSCRIBE,'robot ')
        subscriber.connect(publisher.socket.last_endpoint.decode())
        reads = []
        self.arm.bus.on_read = reads.append
        self.worker.telemetry = publisher
        try:
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                publisher.next_sample = 0
                before = time.time()
                self.worker.cycle(self.arm,.02)
                if subscriber.poll(30): break
            else: self.fail('No measured telemetry reached ZMQ subscriber')
            message = json.loads(subscriber.recv_string().split(' ',1)[1])
            self.assertEqual(message['control']['input'],'imu')
            self.assertEqual(message['control']['feedback'],'measured')
            self.assertFalse(message['control']['enabled'])
            self.assertFalse(message['control']['input_fresh'])
            self.assertIsNone(message['control']['target'])
            self.assertEqual(len(message['pos']),6)
            self.assertGreaterEqual(message['t'],before)
            self.assertLessEqual(message['t'],time.time())
            self.assertEqual(reads,['Present_Position','Present_Load','Torque_Enable']*(len(reads)//3))
            self.assertEqual(self.arm.bus.writes,[])
            # A failed console socket cannot break the hardware control cycle.
            publisher.socket.close()
            publisher.next_sample = 0
            self.worker.cycle(self.arm,.02)
            self.assertIsNotNone(publisher.last_error)
            self.assertTrue(self.worker.snapshot()['connected'])
            self.assertEqual(self.arm.bus.writes,[])
        finally:
            subscriber.close(linger=0)
            context.term()
            publisher.close()


class AutoClawHapticsTests(unittest.TestCase):
    """Default: starting arm tracking also enables the claw and haptic feedback."""
    publish,cycle,enable = PhysicalWearableTests.publish,PhysicalWearableTests.cycle,PhysicalWearableTests.enable

    def setUp(self):
        PhysicalWearableTests.setUp(self)
        self.worker.auto_enable_claw = True
        self.worker.haptics = MagicMock()

    def test_arm_enable_also_enables_claw_and_haptics(self):
        start = self.bus.positions["gripper"]
        self.enable()
        self.assertTrue(self.worker.enabled)
        self.assertTrue(self.worker.claw.enabled)
        self.assertIn(("Goal_Position",{"gripper":start}),self.bus.writes)
        self.assertIn(("Torque_Enable",{"gripper":1}),self.bus.writes)
        self.worker.haptics.enable.assert_called_once_with()
        self.assertTrue(self.worker.snapshot()["claw_enabled"])

    def test_off_center_joystick_keeps_arm_but_skips_claw(self):
        self.publish(joy_y=0)
        self.enable()
        self.assertTrue(self.worker.enabled)
        self.assertFalse(self.worker.claw.enabled)
        self.assertNotIn(("Torque_Enable",{"gripper":1}),self.bus.writes)
        self.assertIn("Claw not auto-enabled",self.worker.snapshot()["message"])
        self.worker.haptics.enable.assert_called_once_with()

    def test_refused_arm_enable_touches_neither_claw_nor_haptics(self):
        self.publish(valid=False)
        self.enable()
        self.assertFalse(self.worker.enabled or self.worker.claw.enabled)
        self.assertEqual(self.bus.writes,[])
        self.worker.haptics.enable.assert_not_called()

    def test_grasp_hold_is_not_released_by_arm_enable(self):
        self.worker.grip_hold_active = True
        self.worker.replay_generation,self.worker.replay_epoch = self.worker.generation,(0,)
        with patch.object(self.worker,'enable_claw_control') as claw:
            self.enable()
        claw.assert_not_called()


if __name__ == "__main__": unittest.main()
