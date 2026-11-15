# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Slider command and shared-worker regressions; no serial ports."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bno085_reader.joystick_calibration import load_calibration
from robot_control.slider import JoystickSlider
from robot_control import test_wearable as fixtures


class SliderBus:
    def __init__(self):
        self.values = dict(Operating_Mode=0,Phase=28,Homing_Offset=0,Min_Position_Limit=0,Max_Position_Limit=0,
                           Present_Position=1000,Torque_Enable=0,Goal_Position=1000)
        self.writes = []
    def read(self,key,name,**kwargs): return self.values[key]
    def write(self,key,name,value,**kwargs):
        self.writes.append((key,value))
        self.values[key] = value


class SliderTests(unittest.TestCase):
    def setUp(self):
        self.bus = SliderBus()
        self.arm = SimpleNamespace(slider_bus=lambda:self.bus)
        self.slider = JoystickSlider(load_calibration())
        self.sample = dict(x=1360,y=1810,pressed=False,seen=10.)

    def enable(self):
        self.slider.set_reference(self.arm,0)
        self.slider.enable(self.arm,self.sample,10.)

    def test_requires_reference_and_center_and_reference_never_commands_movement(self):
        with self.assertRaises(ValueError): self.slider.enable(self.arm,self.sample,10.)
        self.slider.set_reference(self.arm,0)
        self.assertEqual(self.bus.writes,[('Torque_Enable',0)])
        with self.assertRaises(ValueError): self.slider.enable(self.arm,{**self.sample,'x':4095},10.)
        self.assertFalse(self.slider.enabled)
        self.slider.enable(self.arm,self.sample,10.)
        self.assertEqual(self.bus.values['Goal_Position'],1000)

    def test_both_directions_endpoints_and_center_discards_pending_travel(self):
        self.enable()
        for index in range(150):
            self.slider.step({**self.sample,'x':4095},.1,10.)
            self.bus.values['Present_Position'] = self.bus.values['Goal_Position']
        self.assertEqual(self.slider.target,100)
        self.slider.step(self.sample,.01,10.)
        self.assertEqual(self.bus.values['Goal_Position'],self.bus.values['Present_Position'])
        for index in range(150):
            self.slider.step({**self.sample,'x':0},.1,10.)
            self.bus.values['Present_Position'] = self.bus.values['Goal_Position']
        self.assertEqual(self.slider.target,0)
        self.slider.step({**self.sample,'x':4095},.1,10.)
        self.assertNotEqual(self.bus.values['Goal_Position'],self.bus.values['Present_Position'])
        self.slider.step(self.sample,.01,10.)
        self.assertEqual(self.bus.values['Goal_Position'],self.bus.values['Present_Position'])

    def test_no_automatic_motion_on_release_stop_or_fault_recovery(self):
        self.enable()
        self.slider.stop('Stop')
        self.assertEqual(self.bus.values['Torque_Enable'],0)
        count = len(self.bus.writes)
        self.slider.step({**self.sample,'x':4095},.1,10.)
        self.assertEqual(len(self.bus.writes),count)
        self.slider.release(self.arm)
        self.assertIsNone(self.slider.reference)

    def test_stale_input_torque_loss_mode_change_and_counter_reset_block_writes(self):
        for update,sample in (({},dict(seen=9.)),({},dict(pressed=True)),({'Torque_Enable':0},{}),
                              ({'Phase':12},{}),({'Present_Position':-10000},{})):
            self.setUp()
            self.enable()
            self.bus.values.update(update)
            count = len(self.bus.writes)
            with self.assertRaises(ValueError): self.slider.step({**self.sample,**sample},.01,10.)
            self.assertEqual(len(self.bus.writes),count)

    def test_stop_during_slider_read_prevents_goal_write(self):
        self.enable()
        count = len(self.bus.writes)
        def stop(): raise ValueError('operator pause')
        with self.assertRaises(ValueError): self.slider.step(self.sample,.01,10.,before_write=stop)
        self.assertEqual(len(self.bus.writes),count)

    def test_invalid_mode_does_not_change_firmware(self):
        self.bus.values['Phase'] = 12
        with self.assertRaises(ValueError): self.slider.set_reference(self.arm,0)
        self.assertEqual(self.bus.writes,[])

    def test_accumulated_wrong_way_motion_and_stall_are_detected(self):
        self.slider.set_reference(self.arm,0)
        self.bus.values['Present_Position'] = -2000
        self.slider.enable(self.arm,self.sample,10.)
        self.slider.step({**self.sample,'x':4095},.01,10.)
        for i in range(1,8):
            self.bus.values['Present_Position'] = -2000+i*6
            self.slider.step({**self.sample,'x':4095},.01,10.)
        self.bus.values['Present_Position'] = -1952
        with self.assertRaisesRegex(ValueError,'wrong direction'):
            self.slider.step({**self.sample,'x':4095},.01,10.)
        self.setUp()
        self.enable()
        for i in range(30): self.slider.step({**self.sample,'x':4095},.01,10.)
        with self.assertRaisesRegex(ValueError,'stalled'):
            self.slider.step({**self.sample,'x':4095,'seen':12.},.01,12.)

    def test_center_holds_one_target_without_following_encoder_drift(self):
        self.enable()
        self.slider.step({**self.sample,'x':4095},.1,10.)
        self.bus.values['Present_Position'] = 980
        self.slider.step(self.sample,.01,10.)
        self.assertEqual(self.bus.values['Goal_Position'],980)
        self.bus.values['Present_Position'] = 978
        self.slider.step(self.sample,.01,10.)
        self.assertEqual(self.bus.values['Goal_Position'],980)

    def test_slider_view_shares_serial_port_and_decodes_signed_multiturn_values(self):
        from robot_control.arm import Arm
        arm = Arm('TEST')
        view = arm.slider_bus()
        self.assertIs(view.port_handler,arm.bus.port_handler)
        self.assertNotIn('slider',arm.bus.motors)
        with patch.object(view.port_handler,'is_open',True), patch.object(view,'_read',return_value=(32768+446,0,0)):
            self.assertEqual(view.read('Present_Position','slider',normalize=False),-446)
        with patch.object(view.port_handler,'is_open',True), patch.object(view,'_write') as write:
            view.write('Goal_Position','slider',-500,normalize=False)
            self.assertEqual(write.call_args.args[:4],(42,2,7,32768+500))

    def test_worker_uses_x_without_enabling_arm_or_claw_and_stop_releases_slider(self):
        fixture = fixtures.PhysicalWearableTests()
        fixture.setUp()
        fixture.arm.slider_bus = lambda:self.bus
        fixture.worker.request('slider_zero')
        fixture.cycle()
        fixture.worker.request('slider_enable')
        fixture.cycle()
        self.assertTrue(fixture.worker.slider.enabled)
        self.assertFalse(fixture.worker.enabled)
        self.assertFalse(fixture.worker.claw.enabled)
        fixture.packet['joystick']['x'] = 4095
        fixture.worker.submit(fixture.packet)
        fixture.cycle()
        self.assertLess(self.bus.values['Goal_Position'],1000)
        fixture.worker.pause()
        fixture.cycle()
        self.assertFalse(fixture.worker.slider.enabled)
        self.assertEqual(self.bus.values['Torque_Enable'],0)


if __name__=='__main__': unittest.main()
