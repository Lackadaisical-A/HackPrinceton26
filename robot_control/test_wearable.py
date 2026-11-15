# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4"]
# ///
"""Exercise real controller/Arm code against a fake servo bus; never opens a port."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from bno085_reader.joystick_calibration import load_calibration
from robot_control.arm import Arm,BODY,JOINTS
from robot_control.motion_replay import StickButton,load_latest,save_motion
from robot_control.wearable import WearableRobot,bounded_follow,input_packet,load_config
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
        folder = tempfile.TemporaryDirectory()  # Never the real recordings folder.
        self.addCleanup(folder.cleanup)
        self.motions = Path(folder.name)
        self.worker = WearableRobot("TEST",load_calibration(),lambda port:self.arm,lambda:self.now,motion_dir=self.motions)
        self.followed = 0
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

    def tap(self):
        self.publish(pressed=True)
        self.now += .1
        self.publish()

    def follow(self):
        """The servos reach every goal written since the last call."""
        for register,values in self.bus.writes[self.followed:]:
            if register=="Goal_Position": self.bus.positions.update(values)
        self.followed = len(self.bus.writes)

    def step(self,follow=True,**packet):
        self.now += .05
        self.publish(**packet)
        self.cycle()
        if follow: self.follow()

    def record(self,moves):
        """Tap, hand-guide the unpowered arm through joint offsets (one per cycle), tap."""
        start = dict(self.bus.positions)
        self.tap()
        self.cycle()
        for move in moves:
            self.bus.positions.update({n:start[n]+move.get(n,0) for n in JOINTS})
            self.step()
        self.tap()
        self.cycle()
        return self.worker.motion

    def start_replay(self):
        """A recorded sweep, the arm moved elsewhere, Replay requested and under way."""
        motion = self.record([dict(shoulder_pan=20*i,elbow_flex=-10*i,gripper=12*i) for i in range(1,11)])
        self.bus.positions["shoulder_pan"] -= 150
        self.bus.positions["wrist_flex"] += 60
        self.enable("replay")
        for _ in range(3): self.step()
        self.assertIsNotNone(self.worker.replay)
        return motion

    def test_connect_cycle_and_shutdown_are_read_only_without_enable(self):
        for _ in range(3): self.cycle()
        self.assertEqual(self.bus.writes,[])
        self.bus.on_read = lambda register:self.worker.closed.set()
        self.worker.run()
        self.assertEqual(self.bus.writes,[])
        self.assertFalse(self.worker.snapshot()["connected"])

    def test_enable_latches_current_pose_without_jumping_to_urdf_neutral(self):
        self.publish(geometry(.7,1.,.6,.3))
        measured = {n:self.bus.positions[n] for n in BODY}
        self.enable()
        self.assertTrue(self.worker.enabled)
        self.assertEqual(self.bus.writes,[("Goal_Position",measured),("Torque_Enable",dict.fromkeys(BODY,1))])
        self.assertFalse(self.worker.claw.enabled)

    def test_raised_arm_uses_same_mapping_and_bounded_raw_step(self):
        self.enable()
        anchor = dict(self.worker.anchor)
        self.now += .05
        self.publish(geometry(.4,.4,.4,heading=.4))
        self.cycle()
        target = self.bus.writes[-1][1]
        self.assertLess(target["shoulder_lift"],anchor["shoulder_lift"])
        self.assertLess(target["shoulder_pan"],anchor["shoulder_pan"])
        bound = math.ceil(10*4095/360*.05)
        self.assertTrue(all(abs(target[n]-anchor[n])<=bound for n in BODY))

    def test_gap_reset_and_direction_change_require_manual_reenable(self):
        for change in (dict(age=.31),dict(valid=False),dict(epoch=(1,))):
            with self.subTest(change=change):
                self.setUp()
                self.enable()
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

    def test_slow_robot_reads_cannot_use_old_measurements_even_if_input_keeps_up(self):
        self.enable()
        def delay(register):
            self.now += .1
            self.publish()
        self.bus.on_read = delay
        self.cycle()
        self.assertFalse(self.worker.enabled)
        self.assertIn("telemetry too slow",self.worker.snapshot()["message"])

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
        held = self.bus.writes[-1][1]["gripper"]
        self.publish(joy_y=0,pressed=True)  # A press alone is not a stop: the claw keeps its target.
        self.cycle()
        self.assertTrue(self.worker.claw.enabled)
        self.assertEqual(self.bus.writes[-1][1]["gripper"],held)

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
        self.assertLessEqual(previous["shoulder_pan"]-2000,3*4095/360+1e-9)
        limits["shoulder_pan"] = (100,2001)
        out = bounded_follow(values,dict.fromkeys(BODY,0),source,values,values,limits,.1,load_config())
        self.assertEqual(out["shoulder_pan"],2001)
        source["shoulder_pan"] = math.nan
        with self.assertRaises(ValueError): bounded_follow(values,values,source,values,values,limits,.05,load_config())

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

    # Record and replay.

    def test_stick_tap_records_commanded_targets_and_saves_without_pausing(self):
        self.enable("enable_claw")
        self.tap()
        self.cycle()
        self.assertIsNotNone(self.worker.snapshot()["recording"])
        for _ in range(6): self.step(follow=False,joy_y=0)  # Claw commanded open; the fake servo stays put.
        self.tap()
        self.cycle()
        state = self.worker.snapshot()
        self.assertTrue(self.worker.claw.enabled)
        self.assertIsNone(state["recording"])
        self.assertAlmostEqual(state["motion"],.3)
        self.assertIn("saved as motion_",state["message"])
        rows = self.worker.motion
        self.assertEqual(rows[0][1:6],[self.bus.positions[n] for n in BODY])  # Not under control: measured.
        self.assertGreater(rows[-1][6],self.bus.positions["gripper"])         # Under control: the target.
        self.assertEqual(len(list(self.motions.glob("motion_*.json"))),1)
        restarted = WearableRobot("TEST",load_calibration(),lambda port:self.arm,lambda:self.now,motion_dir=self.motions)
        self.assertEqual(restarted.motion,rows)
        self.assertAlmostEqual(restarted.snapshot()["motion"],.3)

    def test_holding_the_stick_pauses_arm_and_claw_and_is_not_a_tap(self):
        self.enable()
        self.enable("enable_claw")
        for _ in range(14): self.step(pressed=True)  # 0.7 s: still only a press.
        self.assertTrue(self.worker.enabled and self.worker.claw.enabled)
        for _ in range(4): self.step(pressed=True)
        self.assertFalse(self.worker.enabled or self.worker.claw.enabled)
        self.assertIn("joystick switch held",self.worker.snapshot()["message"])
        self.assertEqual(self.bus.writes[-1],("Goal_Position",{n:self.bus.positions[n] for n in JOINTS}))
        self.step()
        self.assertIsNone(self.worker.snapshot()["recording"])
        self.assertEqual(list(self.motions.iterdir()),[])

    def test_a_take_too_short_to_replay_is_discarded(self):
        self.tap()
        self.cycle()
        self.tap()
        self.cycle()
        self.assertIn("discarded",self.worker.snapshot()["message"])
        self.assertIsNone(self.worker.motion)
        self.assertEqual(list(self.motions.iterdir()),[])

    def test_recording_stops_itself_at_the_time_limit_and_survives_shutdown(self):
        self.tap()
        self.cycle()
        self.now += 120
        self.publish()
        self.cycle()
        state = self.worker.snapshot()
        self.assertIsNone(state["recording"])
        self.assertAlmostEqual(state["motion"],120)
        self.assertIn("limit",state["message"])
        self.tap()
        self.cycle()
        for _ in range(5): self.step()
        self.bus.on_read = lambda register:self.worker.closed.set()
        self.worker.run()
        state = self.worker.snapshot()
        self.assertIsNone(state["recording"])
        self.assertLess(state["motion"],1)
        self.assertEqual(self.bus.writes,[])

    def test_replay_approaches_the_start_then_follows_the_recording_inside_live_bounds(self):
        # Hand-guided at 20 ticks a cycle, 3.5 times the 10 deg/s the arm may be commanded at.
        motion = self.record([dict(shoulder_pan=20*i,elbow_flex=-10*i,gripper=12*i) for i in range(1,11)])
        first,final = (dict(zip(JOINTS,row[1:])) for row in (motion[0],motion[-1]))
        self.bus.positions["shoulder_pan"] -= 150
        self.bus.positions["wrist_flex"] += 60
        measured = dict(self.bus.positions)
        self.enable("replay")
        self.assertEqual(self.bus.writes,[("Goal_Position",{n:measured[n] for n in BODY}),("Torque_Enable",dict.fromkeys(BODY,1)),
                                          ("Goal_Position",{"gripper":measured["gripper"]}),("Torque_Enable",{"gripper":1})])
        self.assertEqual(self.worker.snapshot()["replay"][0],"approach")
        limit = dict.fromkeys(BODY,math.ceil(10*4095/360*.05))
        limit["gripper"] = math.ceil(.2*(3231-1704)*.05)
        played = 0
        for _ in range(400):
            before = dict(self.bus.positions)
            self.step(valid=False)  # A replay follows the recording: no calibrated wearable input needed.
            self.assertTrue(all(abs(self.bus.positions[n]-before[n])<=limit[n] for n in JOINTS))
            state = self.worker.snapshot()
            if state["replay"] is None: break
            if state["replay"][0]=="play":
                played += 1
                # Slowed down, but still on the recorded path: pan and elbow keep their 2:-1 ratio.
                self.assertLessEqual(abs((self.bus.positions["shoulder_pan"]-first["shoulder_pan"])
                                         +2*(self.bus.positions["elbow_flex"]-first["elbow_flex"])),2)
            else: self.assertEqual(played,0)
        self.assertIn("Replay finished",state["message"])
        self.assertGreater(played,30)  # 200 ticks at 6 a cycle, not the 10 cycles it was recorded in.
        self.assertEqual(self.bus.positions,{n:round(final[n]) for n in JOINTS})
        self.assertFalse(self.worker.enabled or self.worker.claw.enabled)
        self.assertTrue(all(self.bus.torque.values()))
        count = len(self.bus.writes)
        self.step()
        self.assertEqual(len(self.bus.writes),count)  # Holds the final pose; nothing resumes by itself.

    def test_replay_takes_over_from_live_control_and_ignores_enables_until_it_ends(self):
        self.record([dict(shoulder_pan=5*i) for i in range(1,11)])
        self.enable()
        self.enable("enable_claw")
        self.enable("replay")
        self.assertFalse(self.worker.enabled or self.worker.claw.enabled)
        self.assertIsNotNone(self.worker.replay)
        for command in ("enable_arm","enable_claw","replay"): self.worker.request(command)
        self.step()
        self.assertFalse(self.worker.enabled or self.worker.claw.enabled)
        self.assertIsNotNone(self.worker.replay)
        self.assertNotIn("refused",self.worker.snapshot()["message"])

    def test_replay_is_refused_without_a_motion_while_recording_or_under_load(self):
        self.enable("replay")
        self.assertIn("no recorded motion",self.worker.snapshot()["message"])
        self.record([dict(shoulder_pan=5*i) for i in range(1,11)])
        self.bus.loads["elbow_flex"] = 650
        self.enable("replay")
        self.assertIn("high load",self.worker.snapshot()["message"])
        self.bus.loads["elbow_flex"] = 0
        self.tap()
        self.cycle()
        self.enable("replay")
        self.assertIn("stop the recording first",self.worker.snapshot()["message"])
        self.assertIsNotNone(self.worker.snapshot()["recording"])
        self.worker.request("replay")
        self.worker.pause()  # Stop cancels a queued replay like a queued enable.
        self.cycle()
        self.assertIsNone(self.worker.replay)
        self.assertEqual(self.bus.writes,[])

    def test_stick_press_stop_stalled_ui_load_and_torque_loss_abort_a_replay(self):
        def stale_ui(): self.now += .4
        def stick_lost():  # The UI is alive but the hand stick, which is the stop, has gone quiet.
            self.now += .4
            self.packet.update(published=self.now)
            self.worker.submit(self.packet)
        faults = dict(press=lambda:self.publish(pressed=True),stop=lambda:self.worker.pause(),ui=stale_ui,stick=stick_lost,
                      arm_load=lambda:self.bus.loads.update(elbow_flex=650),
                      claw_load=lambda:self.bus.loads.update(gripper=350),
                      torque=lambda:self.bus.torque.update(elbow_flex=0))
        for name,fault in faults.items():
            with self.subTest(fault=name):
                self.setUp()
                self.start_replay()
                fault()
                self.cycle()
                state = self.worker.snapshot()
                self.assertIsNone(state["replay"])
                self.assertIn("PAUSED",state["message"])
                if name in ("ui","stick"): self.assertIn("UI heartbeat" if name=="ui" else "joystick",state["message"])
                # Held where it is; a joint that lost torque is not powered again.
                self.assertEqual(self.bus.writes[-1],("Goal_Position",{n:self.bus.positions[n] for n in JOINTS
                                                                      if self.bus.torque[n]}))
                self.bus.loads = dict.fromkeys(JOINTS,0)
                count = len(self.bus.writes)
                for _ in range(3): self.step()
                self.assertEqual(len(self.bus.writes),count)
                self.assertIsNone(self.worker.snapshot()["recording"])  # The stopping press is not a record tap.

    def test_replay_gives_up_when_the_arm_does_not_follow(self):
        self.start_replay()
        for _ in range(80): self.step(follow=False)
        self.assertIsNone(self.worker.replay)
        self.assertIn("stalled",self.worker.snapshot()["message"])

    def test_malformed_or_foreign_motion_files_are_not_loaded(self):
        good = [[0,*[2000.]*6],[.5,*[2010.]*6]]
        document = lambda **changes:json.dumps({"version":1,"joints":list(JOINTS),"samples":good,**changes})
        for text in ("{",document(version=2),document(joints=list(BODY)),document(samples=good[:1]),
                     document(samples=[good[1],good[0]]),document(samples=[good[0],[.5,*[2010.]*5,4096]]),
                     document(samples=[good[0],[.5,*[2010.]*5,math.nan]]),document(samples=[good[0],good[1][:-1]])):
            with self.subTest(text=text):
                (self.motions/"motion_x.json").write_text(text)
                samples,why = load_latest(self.motions)
                self.assertIsNone(samples)
                self.assertIn("ignored",why)
        (self.motions/"motion_x.json").unlink()
        self.assertEqual(load_latest(self.motions),(None,"no saved motion"))
        name = save_motion(self.motions,good).name
        self.assertEqual(load_latest(self.motions),(good,name))


class StickButtonTests(unittest.TestCase):
    def setUp(self):
        self.button,self.now = StickButton(),0.

    def feed(self,pressed,dt=.02,**changes):
        self.now += dt
        return self.button.update({**dict(sequence=1,epoch=0,seen=self.now,pressed=pressed),**changes},self.now)

    def test_tap_is_a_short_press_and_release(self):
        self.assertEqual([self.feed(p) for p in (False,True,True,False,False)],[None,"press",None,"tap",None])

    def test_hold_fires_once_and_its_release_is_not_a_tap(self):
        self.feed(False)
        self.assertEqual(self.feed(True),"press")
        self.assertIsNone(self.feed(True,.79))
        self.assertEqual(self.feed(True),"hold")
        self.assertEqual([self.feed(True,1),self.feed(False)],[None,None])

    def test_stick_already_down_at_startup_is_ignored_until_released(self):
        self.assertEqual([self.feed(True),self.feed(True,1),self.feed(False)],[None,None,None])
        self.assertEqual(self.feed(True),"press")

    def test_press_across_a_gap_or_board_restart_is_not_a_tap(self):
        for gap in (dict(seen=-1.),dict(sequence=0),dict(epoch=1)):
            with self.subTest(gap=gap):
                self.setUp()
                self.feed(False)
                self.assertEqual(self.feed(True),"press")
                self.assertIsNone(self.feed(True,**gap))
                self.assertFalse(self.button.ready)
                self.assertIsNone(self.feed(False,**({"epoch":1} if "epoch" in gap else {})))
        self.assertIsNone(StickButton().update(None,0.))

    def test_consumed_press_gives_no_tap_or_hold(self):
        self.feed(False)
        self.feed(True)
        self.button.consume()
        self.assertEqual([self.feed(True,1),self.feed(False)],[None,None])


if __name__ == "__main__": unittest.main()
