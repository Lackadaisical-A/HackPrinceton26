# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyzmq>=26", "pyserial>=3.5"]
# ///
"""Cue semantics, serial leases and stop behavior; no physical ports are opened."""
from copy import deepcopy
from pathlib import Path
import sys
import time
import json
import socket
import zmq
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.haptics import HapticFeedback, MOTORS, pattern
from wearable_test.io import WearableHub

JOINTS = ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')


def robot_state(now=10,grip=0,arm=0):
    loads = dict.fromkeys(JOINTS,0)
    loads.update(gripper=grip,shoulder_lift=arm)
    return dict(connected=True,seen=now,loads=loads)


def vision(dx,dy,stamp=100):
    return dict(t=stamp,status='ok',target={'label':'cup'},guidance=dict(dx=dx,dy=dy,aligned=False))


class PatternTests(unittest.TestCase):
    def sample(self,v=None,r=None,now=10,wall=100):
        return pattern(v, r or robot_state(now),now=now,wall=wall,started=10)

    def test_wiring_and_cardinal_and_diagonal_directions(self):
        self.assertEqual(MOTORS,(('wroom',1),('wroom',2),('s3',2),('s3',1)))
        for dx,dy,expected in ((-.5,0,[45,0,45,0]),(.5,0,[0,45,0,45]),
                               (0,-.5,[0,0,45,45]),(0,.5,[45,45,0,0]),
                               (-.5,-.5,[0,0,45,0]),(.5,.5,[0,45,0,0])):
            levels,cue,_ = self.sample(vision(dx,dy))
            self.assertEqual(levels,expected)
            self.assertEqual(cue,'guide')

    def test_taps_have_quiet_gaps_and_deadzone(self):
        self.assertEqual(self.sample(vision(.5,0),now=10.3)[0],[0]*4)
        self.assertEqual(self.sample(vision(.5,0),now=10.7)[0],[0,45,0,45])
        self.assertEqual(self.sample(vision(.1,-.1))[0],[0]*4)

    def test_old_future_missing_degraded_and_bad_guidance_are_silent(self):
        cases = [vision(.5,0,99.4),vision(.5,0,101),vision(float('nan'),0),vision(2,0),
                 {**vision(.5,0),'status':'degraded'}, {**vision(.5,0),'target':None},
                 {**vision(.5,0),'guidance':None},None]
        for value in cases:
            self.assertEqual(self.sample(value)[0],[0]*4,value)

    def test_resistance_is_signed_magnitude_and_uses_correct_body_region(self):
        palm = self.sample(r=robot_state(grip=150))[0]
        self.assertEqual(palm,self.sample(r=robot_state(grip=-150))[0])
        self.assertTrue(28<palm[0]<45)
        self.assertEqual(palm[0],palm[1])
        self.assertEqual(palm[2:],[0,0])
        forearm = self.sample(r=robot_state(arm=300))[0]
        self.assertEqual(forearm[:2],[0,0])
        self.assertTrue(28<forearm[2]<45)
        self.assertEqual(forearm[2],forearm[3])
        self.assertEqual(self.sample(r=robot_state(grip=25,arm=90))[0],[0]*4)

    def test_guidance_adds_taps_on_top_of_steady_resistance_under_cap(self):
        on = self.sample(vision(.5,0),robot_state(grip=250,arm=500))[0]
        off = self.sample(vision(.5,0),robot_state(now=10.3,grip=250,arm=500),now=10.3)[0]
        self.assertEqual(on,[45,65,45,65])
        self.assertEqual(off,[45]*4)
        self.assertEqual(self.sample(vision(.5,0,99),robot_state(grip=250))[0],[45,45,0,0])

    def test_stale_or_invalid_robot_load_never_drives_resistance(self):
        self.assertEqual(self.sample(r=robot_state(now=9,grip=250))[0],[0]*4)
        self.assertEqual(self.sample(r=robot_state(grip=float('nan')))[0],[0]*4)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.now,self.wall = 10.,100.
        self.clock = patch('time.monotonic',side_effect=lambda:self.now)
        self.wallclock = patch('time.time',side_effect=lambda:self.wall)
        self.clock.start(); self.wallclock.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.wallclock.stop)
        self.hub = WearableHub('TEST7','TEST8')
        for name,b in self.hub.boards.items():
            b.connected = True
            b.fields = dict(DRV2605L='1',PWM2='1')
            b.status_at = self.now
            for s in b.samples.values(): s.update(q=(1,0,0,0),seen=self.now)
        self.hub.boards['wroom'].joystick = dict(seen=self.now,pressed=False)
        self.robot = robot_state()
        self.feedback = HapticFeedback(self.hub,SimpleNamespace(snapshot=lambda:deepcopy(self.robot)))

    def renew(self,step):
        self.now += step; self.wall += step
        for b in self.hub.boards.values():
            for sample in b.samples.values(): sample['seen']=self.now
        self.hub.boards['wroom'].joystick['seen'] = self.now
        self.robot['seen'] = self.now
        self.feedback.heartbeat()

    def start_feedback(self):
        self.feedback.enable()
        self.assertTrue(self.feedback.enabled)
        # Drain/ack the intentional initial stops, just as firmware would.
        for b in self.hub.boards.values():
            b.commands(self.now)
            b.ingest('# HAPTIC OFF',self.now)
            b.ingest('# HAPTIC2 OFF',self.now)

    def test_feedback_preserves_pending_ack_and_scales_each_driver(self):
        self.start_feedback()
        self.feedback.vision = vision(.5,0)
        self.feedback.tick()
        b = self.hub.boards['wroom']
        commands = b.commands(self.now)
        self.assertIn('VIBRATE2,500,115',commands)
        pending = b.motors[2]['pending']
        self.hub.intensity('wroom',2,100)
        self.assertEqual(b.motors[2]['percent'],45)
        self.renew(.04); self.feedback.tick()
        self.assertEqual(b.motors[2]['pending'],pending)
        self.assertEqual(b.commands(self.now),[])
        b.ingest('# HAPTIC2 ON 500 115',self.now)
        self.feedback.tick()
        self.assertTrue(self.feedback.snapshot()['acknowledged'][1])
        self.assertFalse(self.feedback.snapshot()['acknowledged'][0])

    def test_stop_and_reset_and_manual_test_cancel_feedback_leases(self):
        for action in (lambda:self.hub.stop(),lambda:self.hub.boards['s3'].reset('USB reset'),
                       lambda:self.hub.motor('wroom',1,30,1)):
            self.setUp_for_loop()
            action()
            self.feedback.tick()
            self.assertFalse(self.feedback.enabled)
            self.assertTrue(all(m['percent']==0 for b in self.hub.boards.values() for m in b.motors.values()))

    def setUp_for_loop(self):
        # Previous cases may reset sensor state; restore healthy samples, never a previous lease.
        for b in self.hub.boards.values():
            b.fields = dict(DRV2605L='1',PWM2='1'); b.status_at=self.now
            for s in b.samples.values(): s.update(q=(1,0,0,0),seen=self.now)
        self.hub.boards['wroom'].joystick = dict(seen=self.now,pressed=False)
        self.start_feedback()
        self.feedback.vision=vision(.5,0)
        self.feedback.tick()

    def test_ack_timeout_latches_off_until_explicit_enable(self):
        self.start_feedback()
        self.robot['loads']['gripper']=200
        self.feedback.tick()
        b=self.hub.boards['wroom']; b.commands(self.now)
        # Keep lease renewed but withhold firmware ACKs.
        for _ in range(8):
            self.renew(.04); self.feedback.tick(); b.commands(self.now)
        self.feedback.tick()
        self.assertFalse(self.feedback.enabled)
        b.ingest('# HAPTIC ON 500',self.now)
        self.feedback.tick()
        self.assertFalse(self.feedback.enabled)
        self.assertIn('acknowledgment',b.message)

    def test_feedback_lease_expiry_latches_fault_and_has_bounded_firmware_pulses(self):
        self.start_feedback()
        self.robot['loads']['gripper']=200; self.feedback.tick()
        b=self.hub.boards['wroom']; b.commands(self.now)
        b.ingest('# HAPTIC ON 500',self.now); b.ingest('# HAPTIC2 ON 500',self.now)
        self.renew(.21)
        self.assertIn('STOP1',b.commands(self.now))
        self.feedback.tick()
        self.assertFalse(self.feedback.enabled)

    def test_intentional_stop_ack_does_not_cancel_new_pulse(self):
        b=self.hub.boards['wroom']
        b.stop(1); b.commands(self.now)
        b.feedback(1,40,self.now); b.commands(self.now)
        b.ingest('# HAPTIC OFF',self.now+.02)
        b.ingest('# HAPTIC ON 500',self.now+.03)
        self.assertEqual(b.motors[1]['percent'],40)
        self.assertEqual(b.motors[1]['acks'],1)

    def test_stale_imu_joystick_and_ui_disarm(self):
        for source in ('imu','joystick','ui'):
            self.setUp_for_loop()
            if source=='imu': self.hub.boards['s3'].samples['s3_imu1']['seen']=self.now-1
            if source=='joystick': self.hub.boards['wroom'].joystick['pressed']=True
            if source=='ui': self.feedback.ui_seen=self.now-1
            self.feedback.tick()
            self.assertFalse(self.feedback.enabled,source)
            self.feedback.heartbeat(); self.robot['seen']=self.now

    def test_robot_offline_removes_resistance_but_preserves_fresh_guidance(self):
        self.start_feedback()
        self.robot['loads']['gripper']=250
        self.feedback.vision=vision(.5,0)
        self.feedback.tick()
        self.assertGreater(self.feedback.snapshot()['motors'][0],0)
        self.robot['seen']=self.now-1
        self.feedback.tick()
        self.assertEqual(self.feedback.snapshot()['motors'],[0,.45,0,.45])
        self.assertIn('resistance unavailable',self.feedback.snapshot()['detail'])


    def test_console_commands_require_fresh_boolean_and_cannot_undo_stop(self):
        for value in ('yes',1,None):
            self.feedback.command(dict(type='set_haptics',enabled=value,t=self.wall))
            self.assertFalse(self.feedback.enabled)
        self.renew(.1)
        self.feedback.command(dict(type='set_haptics',enabled=True,t=self.wall))
        self.assertTrue(self.feedback.enabled)
        queued_at=self.wall
        self.renew(.1); self.feedback.stop()
        self.feedback.command(dict(type='set_haptics',enabled=True,t=queued_at))
        self.assertFalse(self.feedback.enabled)
        self.feedback.command(dict(type='set_haptics',enabled=True,t=self.wall-3))
        self.assertFalse(self.feedback.enabled)
        self.renew(.1)
        self.feedback.command(dict(type='set_haptics',enabled=True,t=self.wall))
        self.assertTrue(self.feedback.enabled)
        self.feedback.command(dict(type='set_haptics',enabled=False,t=self.wall))
        self.assertFalse(self.feedback.enabled)


class NetworkTests(unittest.TestCase):
    def test_real_zmq_commands_vision_and_haptic_output_without_hardware(self):
        class Hub:
            def stop(self): pass
            def begin_feedback(self): return 'lease'
            def feedback(self,levels,token):
                if token!='lease': raise ValueError('bad lease')
            def snapshot(self):
                return {b:{'motors':{m:dict(acks=0,running=False) for m in (1,2)}} for b in ('wroom','s3')}
        context=zmq.Context()
        vp=context.socket(zmq.PUB); cp=context.socket(zmq.PUB)
        vp.bind('tcp://127.0.0.1:0'); cp.bind('tcp://127.0.0.1:0')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0))
            output=f'tcp://127.0.0.1:{reservation.getsockname()[1]}'
        reader=context.socket(zmq.SUB); reader.setsockopt_string(zmq.SUBSCRIBE,'haptics '); reader.connect(output)
        feedback=HapticFeedback(Hub(),SimpleNamespace(snapshot=lambda:robot_state(time.monotonic())),
                                vp.last_endpoint.decode(),cp.last_endpoint.decode(),output)
        feedback.start()
        try:
            for enabled in (True,False):
                deadline=time.monotonic()+4
                observed=False
                while time.monotonic()<deadline:
                    feedback.heartbeat()
                    cp.send_string('command '+json.dumps(dict(t=time.time(),type='set_haptics',enabled=enabled)))
                    vp.send_string('vision '+json.dumps(vision(.5,0,time.time())))
                    if reader.poll(50):
                        message=json.loads(reader.recv_string().split(' ',1)[1])
                        if enabled and message['enabled'] and message['motors']==[0,.45,0,.45]: observed=True; break
                        if not enabled and not message['enabled'] and message['motors']==[0]*4: observed=True; break
                self.assertTrue(observed, f'No correct output for enabled={enabled}')
        finally:
            feedback.close()
            for s in (vp,cp,reader): s.close(linger=0)
            context.term()

if __name__=='__main__': unittest.main()
