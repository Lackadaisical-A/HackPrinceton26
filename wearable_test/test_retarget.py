"""Check retargeted movement in URDF world space, not just joint-value signs."""
import math
import unittest

from wearable_test.io import axis_q
from wearable_test.model import rotate,add,scale
from so101_sim.mapper import multiply
from wearable_test.robot_preview import RobotMapping,URDF,render_model
from wearable_test.retarget import Kinematics


def geometry(upper=0,forearm=0,hand=0,heading=0,roll=0,forearm_heading=None):
    bases = {}
    for role,elevation in (("upper",upper),("forearm",forearm),("hand",hand)):
        yaw = forearm_heading if role != "upper" and forearm_heading is not None else heading
        q = multiply(axis_q(2,yaw),axis_q(1,-elevation))
        if role == "hand": q = multiply(q,axis_q(0,roll))
        bases[role] = tuple(rotate(q,v) for v in ((1,0,0),(0,1,0),(0,0,1)))
    return {"bases":bases}


class RetargetTests(unittest.TestCase):
    def setUp(self):
        self.mapping = RobotMapping()
        self.retarget = self.mapping.retargeter
        self.kinematics = self.retarget.kinematics

    def target(self,g):
        return self.mapping.targets(g,None,0)[0]

    def wrist_reach(self,target):
        frames = self.kinematics.frames(target)
        return add(frames["wrist_flex"][0],scale(frames["shoulder_lift"][0],-1))

    def assertVector(self,actual,expected,tolerance=2e-5):
        for a,b in zip(actual,expected): self.assertAlmostEqual(a,b,delta=tolerance)

    def test_neutral_is_forward_and_palm_down_not_urdf_zero(self):
        target = self.target(geometry())
        directions = self.kinematics.directions(target)
        for role in ("upper","forearm","hand"):
            self.assertVector(directions[role],(1,0,0))
        self.assertVector(directions["palm_up"],(0,0,1))
        self.assertGreater(abs(target["shoulder_lift"]),1)

    def test_raise_and_lower_moves_robot_wrist_in_same_vertical_direction(self):
        heights = []
        for elevation in (-.3,0,.3,.7,1.2):
            target = self.target(geometry(elevation,elevation,elevation))
            heights.append(self.wrist_reach(target)[2])
            self.assertVector(self.kinematics.directions(target)["hand"],(math.cos(elevation),0,math.sin(elevation)))
        self.assertEqual(heights,sorted(heights))
        self.assertLess(heights[0],0)
        self.assertGreater(heights[-1],.2)

    def test_left_right_reach_actually_rotates_base(self):
        for heading in (-.8,-.3,0,.3,.8):
            target = self.target(geometry(.3,.6,.5,heading))
            wrist = self.wrist_reach(target)
            self.assertAlmostEqual(math.atan2(wrist[1],wrist[0]),heading,delta=2e-5)
            self.assertAlmostEqual(target["shoulder_pan"],-heading)

    def test_dangling_bicep_does_not_lock_base_when_forearm_turns(self):
        pans = []
        for heading in (-.6,0,.6):
            target = self.target(geometry(-math.pi/2,0,0,forearm_heading=heading))
            pans.append(target["shoulder_pan"])
        self.assertVector(pans,(.6,0,-.6))

    def test_elbow_ik_tracks_wrist_reach_with_reachable_bend_branch(self):
        for upper,fore in ((0,.5),(0,1.),(-.8,0),(.6,1.3),(.8,-.2)):
            g = geometry(upper,fore,fore,heading=.4)
            target = self.target(g)
            expected = add(scale(g["bases"]["upper"][0],self.retarget.upper_length),
                           scale(g["bases"]["forearm"][0],self.retarget.forearm_length))
            self.assertVector(self.wrist_reach(target),expected)

    def test_roll_matches_hand_palm_orientation(self):
        for roll in (-1.,0,1.):
            g = geometry(.2,.6,.5,heading=.4,roll=roll)
            target = self.target(g)
            self.assertVector(self.kinematics.directions(target)["palm_up"],g["bases"]["hand"][2])

    def test_reverse_is_independent_per_joint(self):
        g = geometry(.3,.5,.5,heading=.6)
        normal = self.target(g)
        self.mapping.reverse["shoulder_pan"] = True
        flipped = self.target(g)
        self.assertAlmostEqual(flipped["shoulder_pan"],-normal["shoulder_pan"])
        for name in normal:
            if name != "shoulder_pan": self.assertAlmostEqual(flipped[name],normal[name])

    def test_vertical_heading_holds_and_limits_are_reported(self):
        self.target(geometry(heading=.5))
        target = self.target(geometry(math.pi/2,math.pi/2,math.pi/2,heading=-2))
        self.assertAlmostEqual(target["shoulder_pan"],-.5)
        self.target(geometry(heading=2.8))
        self.assertIn("shoulder_pan",self.retarget.limited)
        for elevation in (-math.pi,-2,-1,0,1,2,math.pi):
            target = self.target(geometry(elevation,elevation,elevation,heading=2.8,roll=3))
            for name,value in target.items():
                low,high = self.mapping.limits[name]
                self.assertTrue(low <= value <= high,(name,value))

    def test_bending_elbow_does_not_jump_between_ik_branches(self):
        previous = self.target(geometry())
        for step in range(1,151):
            current = self.target(geometry(forearm=step*.01,hand=step*.01))
            for name in ("shoulder_lift","elbow_flex","wrist_flex"):
                self.assertLess(abs(current[name]-previous[name]),.03,(step,name))
            previous = current

    def test_unwrap_does_not_flip_pan_at_pi(self):
        before = self.target(geometry(heading=math.pi-.01))["shoulder_pan"]
        after = self.target(geometry(heading=-math.pi+.01))["shoulder_pan"]
        self.assertEqual(before,after)

    def test_fk_agrees_with_pybullet_link_frames(self):
        import pybullet as p
        client = p.connect(p.DIRECT)
        try:
            robot = p.loadURDF(str(URDF),useFixedBase=True,physicsClientId=client)
            for g in (geometry(),geometry(.4,1.,.7,.6,.5)):
                target = self.target(g)
                frames = self.kinematics.frames(target)
                for i in range(p.getNumJoints(robot,physicsClientId=client)):
                    info = p.getJointInfo(robot,i,physicsClientId=client)
                    name = info[1].decode()
                    if name in target: p.resetJointState(robot,i,target[name],physicsClientId=client)
                for i in range(p.getNumJoints(robot,physicsClientId=client)):
                    name = p.getJointInfo(robot,i,physicsClientId=client)[1].decode()
                    state = p.getLinkState(robot,i,computeForwardKinematics=True,physicsClientId=client)
                    self.assertVector(state[4],frames[name][0],1e-6)
                    xyzw = state[5]
                    wxyz = (xyzw[3],*xyzw[:3])
                    self.assertAlmostEqual(abs(sum(a*b for a,b in zip(wxyz,frames[name][1]))),1,places=6)
        finally: p.disconnect(client)

    def test_display_model_preserves_joint_transforms_and_limits(self):
        display = render_model()
        self.assertNotEqual(display,URDF,"Reduced display meshes were not built or are out of date")
        reduced = Kinematics(display)
        self.assertEqual(reduced.joints,self.kinematics.joints)
        self.assertEqual(reduced.limits,self.kinematics.limits)


if __name__ == "__main__": unittest.main()
