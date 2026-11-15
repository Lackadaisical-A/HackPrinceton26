# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Contact/hold tests use simulated load and a fake bus, never hardware."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.grasp import GraspHold
from robot_control.joystick import JoystickClaw
from robot_control.tuning import apply_values
from robot_control import test_wearable as fixtures


class GraspTests(unittest.TestCase):
    def claw(self):
        claw = JoystickClaw(speed=2,max_lead=.2)
        claw.load_stops_enabled = False
        claw.low,claw.high,claw.previous = 1704,3231,2250
        return claw

    def test_blocked_close_converges_to_hold_without_chasing_closed_goal(self):
        claw = self.claw()
        for i in range(1000):
            # A rigid object stops the jaws at 2250. Simulate proportional servo effort.
            load = min(500,max(0,2250-claw.previous)*15)
            target = claw.position_target(1704,2250,load,.02,i*.02)
        self.assertTrue(claw.grasp.holding)
        self.assertGreaterEqual(target,2238)
        self.assertAlmostEqual(load,120,delta=25)
        # Opening does not spend time unwinding the old closing command.
        self.assertGreater(claw.position_target(3231,2250,load,.02,20),2250)
        self.assertFalse(claw.grasp.holding)

    def test_spikes_opening_motion_empty_endpoint_and_low_load_are_not_grasps(self):
        for mode in ('spike','moving','opening','empty','unloaded'):
            claw = self.claw()
            for i in range(50):
                measured = 2250-i*4 if mode=='moving' else 1710 if mode=='empty' else 2250
                load = 500 if mode!='unloaded' and (mode!='spike' or i%10==0) else 0
                claw.position_target(3231 if mode=='opening' else 1704,measured,load,.02,i*.02)
            self.assertFalse(claw.grasp.holding,mode)

    def test_load_loss_stop_and_opt_in_cutoff_clear_or_reject_hold(self):
        claw = self.claw()
        for i in range(20): claw.position_target(1704,2250,250,.02,i*.02)
        self.assertTrue(claw.grasp.holding)
        claw.load_stops_enabled = True
        with self.assertRaisesRegex(ValueError,'high gripper load'):
            claw.position_target(1704,2250,500,.02,.5)
        claw.load_stops_enabled = False
        for i in range(25): claw.position_target(1704,2250,0,.02,.5+i*.02)
        self.assertFalse(claw.grasp.holding)
        for i in range(20): claw.position_target(1704,2250,250,.02,2+i*.02)
        claw.pause()
        self.assertFalse(claw.grasp.holding)

    def test_loaded_stall_removes_closing_lead_after_confirmation_and_opens_immediately(self):
        claw = self.claw()
        claw.previous = 1950
        for i in range(12):
            target = claw.position_target(1704,2250,500,.01,i*.01)
            self.assertLess(target,2250-12)
            self.assertFalse(claw.grasp.contacted)
        target = claw.position_target(1704,2250,500,.01,.13)
        self.assertGreaterEqual(target,2246)
        self.assertTrue(claw.grasp.holding)
        self.assertGreater(claw.position_target(3231,2250,250,.01,.14),2250)
        self.assertIsNone(claw.grasp.request)

    def test_compressing_object_and_encoder_jitter_do_not_reset_contact_forever(self):
        claw = self.claw()
        for i in range(40):
            measured = 2250-min(i,10)+(2 if i%2 else -2)
            # This never stayed within the old 3-tick anchor window.
            target = claw.position_target(1704,measured,200,.02,i*.02)
            if claw.grasp.holding: self.assertGreaterEqual(target,measured-12)
        self.assertTrue(claw.grasp.holding)

    def test_closed_endpoint_relief_works_even_without_a_large_position_error(self):
        for obstruction in (1704,1710,1723):
            with self.subTest(obstruction=obstruction):
                claw = self.claw()
                claw.previous = 1704
                peak_sustained = 0
                for i in range(250):
                    # Binding at/near the endpoint can require an opening bias,
                    # even when the encoder already reports the desired position.
                    load = min(500,max(0,obstruction-claw.previous+4)*80)
                    if i==0: load = 500
                    target = claw.position_target(1704,obstruction,load,.02,i*.02)
                    if i>20: peak_sustained = max(peak_sustained,load)
                self.assertTrue(claw.grasp.closed_endpoint)
                self.assertFalse(claw.grasp.holding)  # Do not call an empty close an object.
                self.assertLess(peak_sustained,300)
                self.assertGreaterEqual(target,1704)
                self.assertGreater(claw.position_target(3231,obstruction,load,.02,6),obstruction)
                self.assertFalse(claw.grasp.contacted)

    def test_free_motion_keeps_the_configured_full_speed(self):
        claw = self.claw()
        step = claw.speed*(claw.high-claw.low)*.01
        self.assertAlmostEqual(claw.position_target(1704,2250,40,.01,0),2250-step)
        self.assertAlmostEqual(claw.position_target(3231,2250,40,.01,.01),2250)
        self.assertFalse(claw.grasp.contacted)

    def test_unobstructed_gripper_overcomes_running_friction_and_reaches_both_ends(self):
        claw = self.claw()
        measured = claw.previous = 3000.
        now = 0.
        # Servo effort while moving can exceed the contact threshold. A small
        # command cannot overcome gearbox friction, so load-only relief creates
        # its own stall and then falsely calls that stall an object.
        for desired in (2000,1704,3231,1704):
            for _ in range(150):
                error = claw.previous-measured
                velocity = max(-2200,min(2200,(abs(error)-10)*20*(1 if error>0 else -1))) if abs(error)>10 else 0
                measured = max(claw.low,min(claw.high,measured+velocity*.01))
                load = min(500,240+abs(error)*.5) if velocity else min(500,abs(error)*15)
                target = claw.position_target(desired,measured,load,.01,now)
                self.assertFalse(claw.grasp.holding,'Free movement was mistaken for an object')
                if claw.grasp.closed_endpoint:
                    self.assertLessEqual(measured-claw.low,20)
                now += .01
            self.assertAlmostEqual(measured,desired,delta=11)
            self.assertAlmostEqual(target,desired,delta=20 if claw.grasp.closed_endpoint else 1)

    def test_settings_reject_invalid_loads_and_keep_speed(self):
        config = fixtures.load_config()
        result = apply_values(config,{'gripper_contact_load':210,'gripper_hold_load':150})
        self.assertEqual(result['claw_speed_per_s'],config['claw_speed_per_s'])
        for values in ({'gripper_hold_load':200},{'gripper_contact_load':10},
                       {'gripper_hold_load':True},{'gripper_hold_load':float('nan')}):
            with self.assertRaises(ValueError): apply_values(config,values)


class GraspWorkerTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PhysicalWearableTests()
        self.f.setUp()
        self.worker,self.bus = self.f.worker,self.f.bus
        self.worker.config['mapping_mode'] = 'relative'

    def tick(self,follow=False,**packet):
        if follow:
            for register,values in self.bus.writes:
                if register=='Goal_Position':
                    self.bus.positions.update({n:v for n,v in values.items() if n!='gripper'})
                    if 'gripper' in values:
                        self.bus.positions['gripper'] = max(2250,values['gripper'])
                        self.bus.loads['gripper'] = min(500,max(0,2250-values['gripper'])*15)
        self.bus.writes.clear()
        self.f.now += .02
        self.f.publish(**packet)
        self.f.cycle()

    def test_live_grasp_records_closing_intent_and_opening_releases(self):
        self.f.enable('enable_claw')
        for _ in range(80): self.tick(follow=True,joy_y=4095)
        self.assertTrue(self.worker.claw.grasp.holding)
        self.assertTrue(self.worker.snapshot()['grasp']['holding'])
        self.worker.request_replay('record')
        self.tick(follow=True,joy_y=4095)
        self.assertEqual(self.worker.replay.frames[0]['gripper'],1704)
        self.assertEqual(self.bus.positions['gripper'],2250)
        self.tick(follow=True,joy_y=0)
        self.assertFalse(self.worker.claw.grasp.holding)
        self.assertGreater(self.worker.claw.previous,2250)

    def test_replay_accepts_blocked_grasp_and_keeps_regulating_after_completion(self):
        pose = dict(self.bus.positions)
        pose['gripper'] = 1704
        self.worker.replay.record(pose,{n:self.f.arm.limits(n) for n in fixtures.JOINTS},self.f.now)
        self.worker.replay.sample(pose,self.f.now+.2)
        self.worker.replay.stop()
        self.worker.request_replay('play')
        for _ in range(250): self.tick(follow=True)
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertTrue(self.worker.grip_hold_active)
        self.assertTrue(self.worker.claw.grasp.holding)
        self.assertFalse(self.worker.enabled)
        self.assertAlmostEqual(self.bus.loads['gripper'],120,delta=25)
        self.assertIn('holding object',self.worker.replay.detail)
        self.worker.pause('Stop')
        self.tick()
        self.assertFalse(self.worker.grip_hold_active)
        self.assertFalse(self.worker.claw.grasp.holding)
        self.tick()
        self.assertEqual(self.bus.writes,[])

    def test_firmware_overload_and_disconnect_are_not_swallowed_as_contact(self):
        for error in (RuntimeError('[RxPacketError] Overload error!'),ConnectionError('There is no status packet!')):
            with self.subTest(error=error):
                self.setUp()
                self.f.enable('enable_claw')
                def fail(value): raise error
                self.f.arm.move_gripper = fail
                self.tick(joy_y=4095)
                self.assertFalse(self.worker.claw.enabled)
                self.assertFalse(self.worker.claw.grasp.holding)
                self.assertIn(str(error),self.worker.snapshot()['message'])

    def test_replay_accepts_pressure_limited_closed_endpoint_without_an_object_hold(self):
        pose = {**self.bus.positions,'gripper':1704}
        self.worker.replay.record(pose,{n:self.f.arm.limits(n) for n in fixtures.JOINTS},self.f.now)
        self.worker.replay.sample(pose,self.f.now+.2)
        self.worker.replay.stop()
        self.worker.request_replay('play')
        for _ in range(250):
            for register,values in self.bus.writes:
                if register=='Goal_Position':
                    self.bus.positions.update({n:v for n,v in values.items() if n!='gripper'})
                    if 'gripper' in values:
                        self.bus.positions['gripper'] = max(1723,values['gripper'])
                        self.bus.loads['gripper'] = min(500,max(0,1723-values['gripper'])*15)
            self.tick()
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertFalse(self.worker.grip_hold_active)
        self.assertNotIn('stalled',self.worker.replay.detail)
        self.assertNotIn('holding object',self.worker.replay.detail)

    def test_completed_grasp_hold_still_stops_on_stale_input_reset_or_torque_loss(self):
        for fault in ('stale','reset','torque'):
            with self.subTest(fault=fault):
                self.setUp()
                pose = {**self.bus.positions,'gripper':1704}
                self.worker.replay.record(pose,{n:self.f.arm.limits(n) for n in fixtures.JOINTS},self.f.now)
                self.worker.replay.sample(pose,self.f.now+.2)
                self.worker.replay.stop()
                self.worker.request_replay('play')
                for _ in range(150): self.tick(follow=True)
                self.assertTrue(self.worker.grip_hold_active)
                if fault=='torque': self.bus.torque['gripper'] = 0
                self.tick(**({'age':.4} if fault=='stale' else {'epoch':(1,)} if fault=='reset' else {}))
                self.assertFalse(self.worker.grip_hold_active)
                self.tick()
                self.assertEqual(self.bus.writes,[])


if __name__=='__main__': unittest.main()
