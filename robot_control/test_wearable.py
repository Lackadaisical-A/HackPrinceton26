# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4"]
# ///
"""Exercise real controller/Arm code against a fake servo bus; never opens a port."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from bno085_reader.joystick_calibration import load_calibration
from robot_control.arm import Arm,BODY,JOINTS
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
        self.worker = WearableRobot("TEST",load_calibration(),lambda port:self.arm,lambda:self.now)
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

    def test_gap_reset_button_and_direction_change_require_manual_reenable(self):
        for change in (dict(age=.31),dict(valid=False),dict(epoch=(1,)),dict(pressed=True)):
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


if __name__ == "__main__": unittest.main()
