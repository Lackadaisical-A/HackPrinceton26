# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Replay against a fake servo bus: no serial ports or physical movement."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.replay import ClickGestures,MotionReplay
from robot_control import test_wearable as fixtures
from robot_control.test_slider import SliderBus


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.clicks = ClickGestures()
        self.clicks.update(False,0,0)

    def click(self,t):
        self.assertIsNone(self.clicks.update(True,0,t))
        self.assertIsNone(self.clicks.update(True,0,t+.03))
        self.assertIsNone(self.clicks.update(False,0,t+.08))
        return self.clicks.update(False,0,t+.11)

    def test_single_waits_and_double_never_plays(self):
        self.assertIsNone(self.click(.1))
        self.assertIsNone(self.clicks.update(False,0,.4))
        self.assertEqual(self.clicks.update(False,0,.6),'play')
        self.assertIsNone(self.clicks.update(False,0,.8))
        self.assertIsNone(self.click(1))
        self.assertEqual(self.click(1.2),'record')
        self.assertIsNone(self.clicks.update(False,0,2))

    def test_hold_stops_once_and_release_does_not_play(self):
        self.clicks.update(True,0,.1)
        self.clicks.update(True,0,.13)
        self.assertEqual(self.clicks.update(True,0,.84),'stop')
        self.assertIsNone(self.clicks.update(True,0,1))
        self.clicks.update(False,0,1.1)
        self.assertIsNone(self.clicks.update(False,0,1.13))
        self.assertIsNone(self.clicks.update(False,0,2))

    def test_bounce_reset_and_button_held_at_connection_cannot_play(self):
        self.clicks.update(True,0,.1)
        self.clicks.update(False,0,.11)
        self.assertIsNone(self.clicks.update(False,0,.5))
        self.click(1)
        self.clicks.update(False,1,1.2)
        self.assertIsNone(self.clicks.update(False,1,2))
        self.clicks.update(True,2,3)
        self.assertIsNone(self.clicks.update(True,2,4))
        self.clicks.update(False,2,4.1)
        self.clicks.update(False,2,4.13)
        self.assertIsNone(self.clicks.update(False,2,5))


class RecordingTests(unittest.TestCase):
    def setUp(self): self.clip = MotionReplay()

    def record(self):
        self.clip.record({'joint':0},{'joint':(0,100)},10)
        self.clip.sample({'joint':20},10.2)
        self.clip.sample({'joint':100},11)
        self.clip.stop()

    def test_measured_timestamps_interpolate_only_after_return(self):
        self.record()
        self.clip.play(15)
        self.assertEqual(self.clip.target(17),{'joint':0})
        self.assertEqual(self.clip.elapsed,0)
        self.clip.start_timeline(20)
        self.assertAlmostEqual(self.clip.target(20.6)['joint'],60)
        self.assertEqual(self.clip.target(25),{'joint':100})
        self.clip.stop('Disconnected')
        self.assertEqual(self.clip.phase,'ready')
        self.assertEqual(len(self.clip.frames),3)

    def test_invalid_new_recording_preserves_previous_take(self):
        self.record()
        with self.assertRaises(ValueError):
            self.clip.record({'bad':float('nan')},{'bad':(0,1)},30)
        self.assertEqual(self.clip.phase,'ready')
        self.assertEqual(self.clip.limits,{'joint':(0,100)})
        self.assertEqual(self.clip.frames[-1],{'joint':100})

    def test_short_take_refused_and_long_take_capped(self):
        self.clip.record({'j':1},{'j':(0,2)},0)
        self.clip.sample({'j':1},.03)
        self.clip.stop()
        with self.assertRaises(ValueError): self.clip.play(1)
        self.clip.record({'j':1},{'j':(0,2)},0)
        self.clip.sample({'j':2},121)
        self.assertEqual(self.clip.duration,120)
        self.assertEqual(self.clip.phase,'ready')


class ControllerReplayTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PhysicalWearableTests()
        self.f.setUp()
        self.worker,self.bus = self.f.worker,self.f.bus
        self.worker.config['mapping_mode'] = 'relative'
        self.f.enable()

    def tick(self,follow=False,**packet):
        if follow:
            for register,values in self.bus.writes:
                if register=='Goal_Position': self.bus.positions.update(values)
        self.bus.writes.clear()
        self.f.now += .05
        self.f.publish(**packet)
        self.f.cycle()

    def take(self):
        self.first = dict(self.bus.positions)
        self.worker.request_replay('record')
        self.tick()
        for _ in range(6):
            self.bus.positions['shoulder_lift'] += 30
            self.bus.positions['gripper'] += 10
            self.tick()
        self.last = dict(self.bus.positions)
        self.worker.request_replay('record')
        self.tick()
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertEqual({n:self.worker.replay.frames[0][n] for n in fixtures.JOINTS},self.first)
        self.assertEqual({n:self.worker.replay.frames[-1][n] for n in fixtures.JOINTS},self.last)

    def play(self):
        self.worker.request_replay('play')
        self.tick()
        self.assertEqual(self.worker.replay.phase,'returning')
        self.assertEqual(self.bus.writes[0],('Goal_Position',{n:self.last[n] for n in fixtures.BODY}))
        self.assertEqual(self.bus.writes[2],('Goal_Position',{'gripper':self.last['gripper']}))

    def test_return_then_replay_then_hold_without_live_resume(self):
        self.take()
        self.play()
        for _ in range(12): self.tick()
        self.assertEqual(self.worker.replay.phase,'returning')
        self.assertEqual(self.worker.replay.elapsed,0)
        for _ in range(80):
            self.tick(follow=True,pose=fixtures.geometry(forearm=1))
            if not self.worker.replay.busy: break
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertIn('complete',self.worker.replay.detail)
        for name in fixtures.JOINTS: self.assertAlmostEqual(self.bus.positions[name],self.last[name],delta=1)
        self.assertFalse(self.worker.enabled)
        self.assertFalse(self.worker.claw.enabled)
        self.tick()
        self.assertEqual(self.bus.writes,[])

    def test_stale_reset_stop_and_torque_loss_cancel_without_resuming(self):
        for fault in ('stale','epoch','stop','torque'):
            with self.subTest(fault=fault):
                self.setUp()
                self.take()
                self.play()
                if fault=='stop': self.worker.pause()
                if fault=='torque': self.bus.torque['elbow_flex'] = 0
                self.tick(**({'age':.4} if fault=='stale' else {'epoch':(1,)} if fault=='epoch' else {}))
                self.assertFalse(self.worker.replay.busy)
                self.assertEqual(self.worker.replay.phase,'ready')
                self.tick()
                self.assertEqual(self.bus.writes,[])

    def test_unreachable_first_pose_stalls_without_starting_timeline(self):
        self.take()
        self.play()
        for _ in range(130):
            self.tick()
            if not self.worker.replay.busy: break
        self.assertFalse(self.worker.replay.busy)
        self.assertEqual(self.worker.replay.elapsed,0)
        self.assertIn('stalled',self.worker.replay.detail)

    def test_short_physical_clicks_record_and_play_and_press_stops_replay(self):
        def click():
            for _ in range(2): self.tick(pressed=True)
            for _ in range(2): self.tick(pressed=False)
        click()
        self.assertTrue(self.worker.enabled)
        click()
        self.assertEqual(self.worker.replay.phase,'recording')
        for _ in range(10): self.tick()
        click()
        click()
        self.assertEqual(self.worker.replay.phase,'ready')
        for _ in range(10): self.tick()
        self.assertFalse(self.worker.replay.busy)
        click()
        for _ in range(8): self.tick()
        self.assertTrue(self.worker.replay.busy)
        for _ in range(2): self.tick(pressed=True)
        self.assertFalse(self.worker.replay.busy)

    def test_stale_revision_and_stop_cancel_queued_play(self):
        self.take()
        self.worker.request_replay('play',revision=-1,request_id='old')
        self.tick()
        self.assertFalse(self.worker.replay.busy)
        self.assertFalse(self.worker.replay.request['ok'])
        self.worker.request_replay('play')
        self.worker.pause()
        self.tick()
        self.assertFalse(self.worker.replay.busy)
        self.assertFalse(any(r=='Torque_Enable' for r,_ in self.bus.writes))

    def test_slider_recording_uses_current_reference_and_replays_to_endpoint(self):
        slider_bus = SliderBus()
        self.f.arm.slider_bus = lambda:slider_bus
        self.worker.request('slider_zero')
        self.tick()
        self.worker.request('slider_enable')
        self.tick()
        self.take()
        self.assertTrue(self.worker.replay.has_slider)
        self.assertEqual(self.worker.replay.frames[0]['slider'],0)
        # Place the simulated carriage a little away from the recorded first endpoint.
        slider_bus.values['Present_Position'] -= 120
        self.worker.request_replay('play')
        self.tick()
        self.assertEqual(self.worker.replay.phase,'returning')
        for _ in range(80):
            slider_bus.values['Present_Position'] = slider_bus.values['Goal_Position']
            self.tick(follow=True)
            if not self.worker.replay.busy: break
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertIn('complete',self.worker.replay.detail)
        self.assertEqual(slider_bus.values['Present_Position'],1000)
        self.assertEqual(slider_bus.values['Torque_Enable'],0)
        self.worker.slider.reference = None
        self.worker.request_replay('play',request_id='missing-reference')
        self.tick()
        self.assertFalse(self.worker.replay.request['ok'])
        self.assertIn('Reference the slider',self.worker.replay.detail)
        self.assertFalse(any(r=='Torque_Enable' for r,_ in self.bus.writes))

    def test_replay_honors_adjusted_speed_and_opt_in_load_stops(self):
        self.take()
        self.worker.tune({'speed_deg_s':10,'claw_speed_per_s':.1,'load_stops_enabled':True})
        self.play()
        self.tick()
        for register,values in self.bus.writes:
            if register!='Goal_Position': continue
            for name,value in values.items():
                bound = .1*(3231-1704)*.05 if name=='gripper' else 10*4095/360*.05
                self.assertLessEqual(abs(value-self.last[name]),bound+1)
        self.bus.loads['elbow_flex'] = 700
        self.tick()
        self.assertFalse(self.worker.replay.busy)
        self.assertIn('load',self.worker.replay.detail.lower())

    def test_serial_failure_publishes_stopped_replay_and_preserves_take(self):
        self.take()
        self.play()
        def fail(register): raise ConnectionError('Motor read failed')
        self.bus.on_read = fail
        with self.assertLogs('wearable',level='INFO'): self.worker.run()
        status = self.worker.snapshot()
        self.assertFalse(status['connected'])
        self.assertEqual(status['replay']['phase'],'ready')
        self.assertEqual(status['replay']['samples'],len(self.worker.replay.frames))
        self.assertFalse(self.worker.replay.busy)


if __name__=='__main__': unittest.main()
