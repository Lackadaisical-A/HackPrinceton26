import math
import multiprocessing
import unittest
from unittest.mock import MagicMock

from wearable_test.io import DemoHub
from wearable_test.model import ArmModel
from wearable_test.robot_preview import RobotMapping, RobotPreview, render_worker, URDF


class PreviewTests(unittest.TestCase):
    def test_calibrated_mounts_drive_actual_robot_geometry(self):
        hub = DemoHub()
        model = ArmModel()
        model.calibrate(hub.frame("A"),hub.frame("B"))
        mapping = RobotMapping()
        neutral,_ = mapping.targets(model.geometry(hub.frame("A")),None,1)
        lowered,_ = mapping.targets(model.geometry(hub.frame("B")),None,1)
        kinematics = mapping.retargeter.kinematics
        for role in ("upper","forearm","hand"):
            self.assertGreater(kinematics.directions(neutral)[role][0],.9999)
        self.assertLess(kinematics.directions(lowered)["forearm"][2],-.5)
        moving,_ = mapping.targets(model.geometry(hub.frame(None,1)),None,1)
        self.assertNotAlmostEqual(moving["elbow_flex"],0)
        for name,value in moving.items():
            low,high = mapping.limits[name]
            self.assertTrue(low <= value <= high)

    def test_joystick_exact_urdf_limits_and_stale_button_hold(self):
        mapping = RobotMapping()
        geometry = ArmModel().geometry()
        low,high = mapping.limits["gripper"]
        for raw,expected in ((0,high),(1810,(low+high)/2),(4095,low)):
            target,_ = mapping.targets(geometry,dict(y=raw,seen=1,pressed=False),1)
            self.assertAlmostEqual(target["gripper"],expected)
        for joystick in (None,dict(y=0,seen=0,pressed=False),dict(y=0,seen=1,pressed=True)):
            target,_ = mapping.targets(geometry,joystick,1)
            self.assertEqual(target["gripper"],mapping.current["gripper"])

    def test_reversing_and_fast_frame_rate_independent_response(self):
        mapping = RobotMapping()
        mapping.reverse["gripper"] = True
        target,_ = mapping.targets(ArmModel().geometry(),dict(y=0,seen=1,pressed=False),1)
        self.assertEqual(target["gripper"],mapping.limits["gripper"][0])
        previous = dict(mapping.current)
        mapping.advance(target,0)
        self.assertEqual(mapping.current,previous)
        for _ in range(6): mapping.advance(target,.02)
        initial_error = abs(target["gripper"]-previous["gripper"])
        self.assertLess(abs(mapping.current["gripper"]-target["gripper"]),.04*initial_error)
        other = RobotMapping()
        for _ in range(3): other.advance(target,.04)
        for name in target: self.assertAlmostEqual(mapping.current[name],other.current[name])

    def make_preview(self):
        preview = object.__new__(RobotPreview)
        preview.mapping = RobotMapping()
        preview.closed,preview.running,preview.valid_inputs,preview.last = False,True,True,0
        preview.stale_since = None
        preview.input_updated_at,preview.pose_updated_at,preview.pose_number = 0,None,0
        preview.hold_reason = ""
        preview.status,preview.pause_button,preview.window = MagicMock(),MagicMock(),MagicMock()
        preview.on_stop = MagicMock()
        preview.on_pause = None
        preview.labels = {joint:MagicMock() for joint in preview.mapping.current}
        return preview

    def test_directions_are_shared_with_wearable_lab_and_follow_web_changes(self):
        preview = self.make_preview()
        shared = dict.fromkeys(preview.mapping.current, False)
        preview.mapping.reverse = shared
        variable = MagicMock()
        variable.get.return_value = False
        preview.reverse_vars = {'wrist_roll': variable}
        changes = []
        preview.on_reverse = lambda joint, value: changes.append((joint, value))
        preview.set_reverse('wrist_roll', True)
        self.assertEqual(changes, [('wrist_roll', True)])
        shared['wrist_roll'] = True  # As Wearable Lab does after a web-console request.
        preview.update(ArmModel().geometry(), None, False, False, 1., 'Recenter arm')
        variable.set.assert_called_once_with(True)

    def test_reference_hold_resumes_after_recenter_without_latching_pause(self):
        preview = self.make_preview()
        geometry = ArmModel().geometry()
        held = dict(preview.mapping.current)
        preview.update(geometry,None,False,True,1,"COM8: IMU RESET imu1; Recenter arm")
        self.assertTrue(preview.running)
        self.assertFalse(preview.valid_inputs)
        self.assertEqual(preview.mapping.current,held)
        self.assertIn("IMU RESET imu1",preview.hold_reason)
        preview.calibration_changed()
        preview.update(geometry,None,True,True,10)
        self.assertTrue(preview.running)
        self.assertEqual(preview.hold_reason,"")
        self.assertEqual(preview.pose_number,1)

    def test_manual_pause_persists_across_gaps_and_recenter(self):
        preview = self.make_preview()
        geometry = ArmModel().geometry()
        preview.stop_all()
        self.assertFalse(preview.running)
        preview.on_stop.assert_called_once()
        preview.update(geometry,None,False,False,1)
        preview.calibration_changed()
        preview.update(geometry,None,True,True,2)
        self.assertFalse(preview.running)
        self.assertEqual(preview.pose_number,0)
        preview.toggle()
        self.assertTrue(preview.running)

    def test_long_data_gap_recovers_without_manual_resume(self):
        preview = self.make_preview()
        geometry = ArmModel().geometry()
        preview.update(geometry,None,True,False,1.2)
        self.assertTrue(preview.running)
        held = dict(preview.mapping.current)
        preview.update(geometry,None,True,False,1.5)
        self.assertEqual(preview.mapping.current,held)
        preview.update(geometry,None,True,True,1.6)
        self.assertTrue(preview.running)
        self.assertIsNone(preview.stale_since)
        preview.update(geometry,None,True,False,2)
        preview.update(geometry,None,True,False,14.1)
        self.assertTrue(preview.running)
        preview.update(geometry,None,True,True,14.2)
        self.assertEqual(preview.hold_reason,"")
        self.assertEqual(preview.pose_updated_at,14.2)

    def test_pose_error_recovers_with_next_valid_input(self):
        preview = self.make_preview()
        preview.update({},None,True,True,1)
        self.assertIn("retrying",preview.hold_reason)
        self.assertTrue(preview.running)
        preview.update(ArmModel().geometry(),None,True,True,1.1)
        self.assertEqual(preview.hold_reason,"")

    def test_real_urdf_loads_with_six_joints_and_renders(self):
        import pybullet as p
        client = p.connect(p.DIRECT)
        try:
            robot = p.loadURDF(str(URDF),useFixedBase=True,physicsClientId=client)
            joints = [p.getJointInfo(robot,i,physicsClientId=client) for i in range(p.getNumJoints(robot,physicsClientId=client))]
            movable = {info[1].decode() for info in joints if info[2] != p.JOINT_FIXED}
            self.assertEqual(movable,set(RobotMapping().current))
            view = p.computeViewMatrixFromYawPitchRoll([-.05,0,.18],.75,130,-25,0,2)
            projection = p.computeProjectionMatrixFOV(45,1,.01,5)
            result = p.getCameraImage(160,160,viewMatrix=view,projectionMatrix=projection,
                                      renderer=p.ER_TINY_RENDERER,physicsClientId=client)
            self.assertEqual(result[:2],(160,160))
            pixels = result[2].tobytes() if hasattr(result[2],"tobytes") else bytes(result[2])
            self.assertEqual(len(pixels),160*160*4)
            self.assertTrue(any(pixels[i:i+3] != b"\xff\xff\xff" for i in range(0,len(pixels),4)))
        finally:
            p.disconnect(client)

    def test_separate_renderer_process_returns_frame_and_exits(self):
        context = multiprocessing.get_context("spawn")
        parent,child = context.Pipe()
        process = context.Process(target=render_worker,args=(child,))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10),"Renderer did not initialize")
            self.assertEqual(parent.recv(),("ready",))
            parent.send({"pose":RobotMapping().current,"camera":(1.,130,-25),"size":(160,100)})
            self.assertTrue(parent.poll(10),"Renderer did not produce a frame")
            result = parent.recv()
            self.assertEqual(result[:3],("frame",160,100))
            self.assertEqual(len(result[3]),160*100*4)
            parent.send(None)
            process.join(timeout=3)
            self.assertFalse(process.is_alive())
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)


if __name__ == "__main__": unittest.main()
