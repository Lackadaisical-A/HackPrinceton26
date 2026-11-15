# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Smart replay against fakes: no serial ports, no network, no physical movement."""
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.reach import direction_vector,step_joints
from robot_control.replay import MotionReplay
from robot_control.smart_replay import SmartReplay
from robot_control import test_wearable as fixtures
from robot_control.test_replay import ControllerReplayTests


class RecordingMarksTests(unittest.TestCase):
    def take(self):
        clip = MotionReplay()
        clip.record({'joint':0,'gripper':80},{'joint':(0,100),'gripper':(0,100)},10,wall=1000.)
        for i in range(1,101):
            clip.note_holding(30<=i<70,10+i*.1)
            clip.sample({'joint':i,'gripper':80},10+i*.1)
        clip.stop()
        return clip

    def test_grasp_and_release_are_marked_once_and_become_waypoints(self):
        clip = self.take()
        self.assertEqual([e['kind'] for e in clip.events],['grasp','release'])
        self.assertAlmostEqual(clip.events[0]['t'],3.,places=2)
        self.assertAlmostEqual(clip.events[1]['t'],7.,places=2)
        points = clip.snapshot()['waypoints']
        self.assertEqual(points,sorted(points))
        self.assertEqual((points[0],points[-1]),(0.,10.))
        for event in clip.events: self.assertIn(event['t'],points)
        self.assertLessEqual(len(points),MotionReplay.MAX_WAYPOINTS)
        self.assertEqual(clip.snapshot()['recorded_wall'],1000.)

    def test_pose_at_any_recorded_time_and_new_take_clears_marks(self):
        clip = self.take()
        self.assertAlmostEqual(clip.target_at(4.25)['joint'],42.5)
        self.assertEqual(clip.target_at(99)['joint'],100)
        clip.record({'joint':0,'gripper':80},{'joint':(0,100),'gripper':(0,100)},50)
        self.assertEqual((clip.events,clip.points,clip.agent),([],[],None))

    def test_marks_ignored_outside_a_recording(self):
        clip = self.take()
        clip.note_holding(True,99)
        self.assertEqual(len(clip.events),2)


class Arrival:
    """Arrives once every joint is within one unit; raises like PoseArrival when told to stall."""
    def __init__(self,target,stall): self.target,self.stall = target,stall
    def update(self,measured,now,grasped):
        if self.stall(): raise ValueError('Replay positioning stalled: joint')
        return all(abs(measured[n]-v)<=1 or (n=='gripper' and grasped) for n,v in self.target.items())


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.clip = MotionReplay()
        limits = {'shoulder_pan':(0,1000),'gripper':(100,900)}
        self.clip.record({'shoulder_pan':0,'gripper':800},limits,0)
        for i in range(1,41): self.clip.sample({'shoulder_pan':i*10,'gripper':800},i*.1)
        self.clip.stop()
        self.now = 100.
        self.pose = {'shoulder_pan':50.,'gripper':800.}
        self.grasp = SimpleNamespace(holding=False,closed_endpoint=False,contacted=False)
        self.stall = False
        tools = SimpleNamespace(
            nudge=lambda pose,direction,cm:{**pose,'shoulder_pan':pose['shoulder_pan']+cm*(10 if direction=='left' else -10)},
            shift=lambda pose,degrees:{**pose,**{n:pose[n]+v for n,v in degrees.items()}},
            turn=lambda pose,degrees:{**pose,'shoulder_pan':pose['shoulder_pan']+degrees})
        self.smart = SmartReplay(self.clip,lambda pose,positions:Arrival(pose,lambda:self.stall),tools,self.now,self.pose)

    def tick(self,count=1,speed=60):
        """Step the executor; the fake arm moves toward each commanded pose."""
        for _ in range(count):
            self.now += .05
            target = self.smart.step(self.now,dict(self.pose),dict(self.pose),dict(self.pose),self.grasp)
            for name,value in target.items():
                self.pose[name] += max(-speed,min(speed,value-self.pose[name]))

    def act(self,ticks=40,**action):
        self.assertTrue(self.smart.submit({'seq':self.smart.seq,**action},self.now))
        self.tick(ticks)
        return self.smart.result

    def test_waits_holding_still_and_ignores_stale_or_early_answers(self):
        self.tick(5)
        self.assertEqual((self.smart.state,self.pose['shoulder_pan']),('waiting',50.))
        self.assertFalse(self.smart.submit({'seq':7,'action':'done'},self.now))
        self.assertFalse(self.smart.submit(None,self.now))
        self.assertEqual(self.smart.state,'waiting')

    def test_waypoint_travel_keeps_the_gripper_and_reports(self):
        self.pose['gripper'] = 300.
        self.smart.hold['gripper'] = 300.
        last = len(self.clip.points)-1
        result = self.act(action='go_to_waypoint',waypoint=last,why='  go  to the bin ')
        self.assertTrue(result['ok'])
        self.assertEqual((result['seq'],self.smart.seq,self.smart.state),(1,2,'waiting'))
        self.assertAlmostEqual(self.pose['shoulder_pan'],400)
        self.assertEqual(self.pose['gripper'],300)
        self.assertEqual(self.smart.action['why'],'go to the bin')

    def test_segment_plays_at_recorded_speed_from_its_first_waypoint(self):
        self.assertTrue(self.smart.submit({'seq':1,'action':'play_segment','waypoint':0,'to':len(self.clip.points)-1},self.now))
        self.tick(3)
        self.assertEqual(self.smart.stages[0][0],'play')
        started = self.now
        while self.smart.state=='acting': self.tick()
        self.assertAlmostEqual(self.now-started,self.clip.duration,delta=.3)
        self.assertTrue(self.smart.result['ok'])
        self.assertAlmostEqual(self.pose['shoulder_pan'],400)

    def test_close_reports_a_hold_or_a_miss(self):
        self.smart.submit({'seq':1,'action':'grip','grip':'close'},self.now)
        self.tick(3)
        self.assertEqual(self.smart.state,'acting')
        self.grasp.holding = self.grasp.contacted = True
        self.tick()
        self.assertEqual(self.smart.result,dict(seq=1,ok=True,action='grip',detail='Holding an object.'))
        self.grasp.holding = False
        self.smart.submit({'seq':2,'action':'grip','grip':'close'},self.now)
        self.grasp.closed_endpoint = True
        self.tick()
        self.assertFalse(self.smart.result['ok'])
        self.assertIn('Closed on nothing',self.smart.result['detail'])
        self.assertEqual(self.smart.state,'waiting')

    def test_open_goes_to_the_recorded_open_position(self):
        self.pose['gripper'] = 200.
        self.smart.hold['gripper'] = 200.
        self.assertTrue(self.act(action='grip',grip='open')['ok'])
        self.assertAlmostEqual(self.pose['gripper'],800,delta=30)

    def test_drop_and_stall_become_results_and_the_arm_holds_where_it_is(self):
        self.grasp.holding = True
        self.tick()
        self.smart.submit({'seq':1,'action':'go_to_waypoint','waypoint':len(self.clip.points)-1},self.now)
        self.tick(2)
        self.grasp.holding = False
        self.tick()
        self.assertIn('dropped',self.smart.result['detail'])
        held = self.pose['shoulder_pan']
        self.tick(5)
        self.assertEqual(self.pose['shoulder_pan'],held)
        self.smart.submit({'seq':2,'action':'go_to_waypoint','waypoint':0},self.now)
        self.stall = True
        self.tick()
        self.assertIn('stalled',self.smart.result['detail'])
        self.assertEqual((self.smart.state,self.smart.seq),('waiting',3))

    def test_a_recorded_release_is_not_a_drop(self):
        self.grasp.holding = True
        self.tick()
        self.smart.submit({'seq':1,'action':'play_segment','waypoint':0,'to':2},self.now)
        self.tick(3)
        self.grasp.holding = False
        while self.smart.state=='acting': self.tick()
        self.assertTrue(self.smart.result['ok'])

    def test_bad_actions_are_reported_not_executed(self):
        for action in (dict(action='fly'),dict(action='go_to_waypoint',waypoint=99),dict(action='go_to_waypoint',waypoint=1.5),
                       dict(action='play_segment',waypoint=3,to=1),dict(action='nudge',direction='sideways',amount=1),
                       dict(action='nudge',direction='left',amount=-1),dict(action='move_joints',joints={'gripper':5}),
                       dict(action='grip',grip='squeeze')):
            self.setUp()
            result = self.act(ticks=1,**action)
            self.assertFalse(result['ok'],action)
            self.assertEqual(self.pose['shoulder_pan'],50.)

    def test_moves_are_capped_and_clamped_to_recorded_limits(self):
        self.act(action='nudge',direction='left',amount=50)
        self.assertEqual(self.smart.action['amount'],5)
        self.assertAlmostEqual(self.pose['shoulder_pan'],100)
        self.act(action='move_joints',joints={'shoulder_pan':-400})
        self.assertAlmostEqual(self.pose['shoulder_pan'],90)
        self.pose['shoulder_pan'] = self.smart.hold['shoulder_pan'] = 5.
        result = self.act(action='move_joints',joints={'shoulder_pan':-10})
        self.assertEqual(self.pose['shoulder_pan'],0)
        self.assertIn('clamped',result['detail'])

    def test_look_can_turn_and_is_capped(self):
        self.assertTrue(self.act(action='look',pan=90)['ok'])
        self.assertAlmostEqual(self.pose['shoulder_pan'],75)
        self.assertTrue(self.act(action='look')['ok'])
        self.assertAlmostEqual(self.pose['shoulder_pan'],75)

    def test_repeated_failures_hand_over_to_the_operator(self):
        for _ in range(4): self.act(ticks=1,action='fly')
        self.assertEqual(self.smart.state,'asking')
        self.assertFalse(self.smart.submit({'seq':self.smart.seq,'action':'done'},self.now))
        self.smart.resume(self.now)
        self.assertEqual(self.smart.state,'waiting')
        self.assertIn('operator',self.smart.result['detail'])
        with self.assertRaises(ValueError): self.smart.resume(self.now)

    def test_ask_done_and_the_limits_end_or_pause_the_run(self):
        self.smart.submit({'seq':1,'action':'ask_operator','text':'The bin is missing.'},self.now)
        self.assertEqual((self.smart.state,self.smart.detail),('asking','The bin is missing.'))
        self.now += 500
        self.tick()
        self.assertEqual(self.smart.state,'asking')  # The operator is not on Gemini's clock.
        self.smart.resume(self.now)
        self.smart.submit({'seq':2,'action':'done','text':'All sorted.'},self.now)
        self.assertTrue(self.smart.finished)
        self.assertEqual(self.smart.detail,'All sorted.')
        self.setUp()
        self.now += 121
        self.tick()
        self.assertIn('did not choose',self.smart.detail)
        self.setUp()
        self.smart.config['max_actions'] = 2
        self.act(ticks=10,action='look')
        self.act(ticks=10,action='look')
        self.assertTrue(self.smart.finished)
        self.setUp()
        self.now += 601
        self.tick()
        self.assertIn('time limit',self.smart.detail)


class ReachTests(unittest.TestCase):
    @staticmethod
    def tip(angles):
        reach = .12*math.cos(angles['lift'])+.12*math.cos(angles['lift']+angles['elbow'])
        height = .12*math.sin(angles['lift'])+.12*math.sin(angles['lift']+angles['elbow'])
        return (reach*math.cos(angles['pan']),reach*math.sin(angles['pan']),height)

    def test_each_direction_moves_the_tip_by_the_step(self):
        start = dict(pan=.4,lift=1.,elbow=-1.4)
        here = self.tip(start)
        for direction in ('left','right','forward','back','up','down'):
            delta = direction_vector(here,direction,.01)
            solved = step_joints(self.tip,start,delta,('pan','lift','elbow'))
            there = self.tip(solved)
            for axis in range(3): self.assertAlmostEqual(there[axis]-here[axis],delta[axis],delta=.001)
        forward = direction_vector(here,'forward',.02)
        self.assertGreater(forward[0]*here[0]+forward[1]*here[1],0)
        self.assertGreater(direction_vector((1,0,0),'left',1)[1],0)
        self.assertEqual(direction_vector(here,'up',.02),(0.,0.,.02))

    def test_unreachable_or_large_steps_are_refused(self):
        with self.assertRaises(ValueError):
            step_joints(self.tip,dict(pan=0.,lift=.01,elbow=.01),(.05,0,0),('pan','lift','elbow'))
        with self.assertRaises(ValueError):
            step_joints(self.tip,dict(pan=0.,lift=1.,elbow=-1.4),(0,.02,0),('pan','lift','elbow'),
                        limits=dict(pan=(-.01,.01),lift=(-3,3),elbow=(-3,3)))
        with self.assertRaises(ValueError): direction_vector((1,0,0),'sideways',.01)


class ControllerSmartTests(ControllerReplayTests):
    """The executor inside the real control cycle, on the fake servo bus."""
    def setUp(self):
        super().setUp()
        self.inbox = None
        self.worker.agent_inbox = SimpleNamespace(latest=lambda:self.inbox)

    def start(self):
        self.take()
        self.worker.request_replay('play_smart')
        self.tick()
        self.assertEqual(self.worker.replay.phase,'smart')

    def agent(self): return self.worker.snapshot()['replay']['agent']

    def answer(self,**action): self.inbox = dict(run=self.agent()['run'],**action)

    def test_gemini_actions_move_the_arm_and_done_ends_the_run(self):
        self.start()
        for _ in range(5): self.tick(follow=True)
        for name in fixtures.BODY: self.assertAlmostEqual(self.bus.positions[name],self.last[name],delta=1)
        state = self.agent()
        self.assertEqual((state['state'],state['seq']),('waiting',1))
        self.assertEqual(set(state['arm']['joints']),set(fixtures.BODY))
        self.assertIn('holding',state['arm'])
        self.inbox = dict(seq=1,action='go_to_waypoint',waypoint=0)
        for _ in range(3): self.tick(follow=True)
        self.assertEqual(self.agent()['state'],'waiting')  # No run name: an answer to some other run.
        self.answer(seq=1,action='go_to_waypoint',waypoint=0,why='back to the start')
        for _ in range(120):
            self.tick(follow=True)
            if self.agent()['seq']==2: break
        self.assertTrue(self.agent()['result']['ok'],self.agent())
        for name in fixtures.BODY: self.assertAlmostEqual(self.bus.positions[name],self.first[name],delta=6)
        self.assertEqual(self.bus.positions['gripper'],self.last['gripper'])
        self.answer(seq=2,action='done',text='Finished.')
        self.tick(follow=True)
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertIn('Finished.',self.worker.replay.detail)
        self.assertEqual(self.agent()['state'],'done')
        self.tick()
        self.assertEqual(self.bus.writes,[])

    def test_refused_without_the_assist_link_and_plain_replay_is_unchanged(self):
        self.worker.agent_inbox = None
        self.take()
        self.worker.request_replay('play_smart',request_id='a')
        self.tick()
        self.assertEqual(self.worker.replay.phase,'ready')
        self.assertFalse(self.worker.replay.request['ok'])
        self.assertIn('assist',self.worker.replay.request['detail'])
        self.play()

    def test_joystick_press_stale_input_and_stop_end_smart_replay(self):
        for fault in ('press','stale','stop'):
            with self.subTest(fault=fault):
                self.setUp()
                self.start()
                self.answer(seq=1,action='go_to_waypoint',waypoint=0)
                self.tick(follow=True)
                if fault=='stop': self.worker.pause()
                if fault=='press':
                    for _ in range(2): self.tick(pressed=True)
                else: self.tick(**({'age':.4} if fault=='stale' else {}))
                self.assertFalse(self.worker.replay.busy)
                self.assertEqual(self.agent()['state'],'done')
                self.tick()
                self.assertEqual(self.bus.writes,[])

    def test_resume_only_while_smart_replay_asks(self):
        self.start()
        self.worker.request_replay('resume',request_id='r')
        self.tick()
        self.assertFalse(self.worker.replay.request['ok'])
        self.answer(seq=1,action='ask_operator',text='Check the bin.')
        self.tick(follow=True)
        self.assertEqual(self.agent()['state'],'asking')
        self.assertEqual(self.worker.replay.detail,'Check the bin.')
        self.worker.request_replay('resume',request_id='s')
        self.tick(follow=True)
        self.assertTrue(self.worker.replay.request['ok'])
        self.assertEqual(self.agent()['state'],'waiting')

    def test_nudge_moves_the_real_urdf_tip_straight_up(self):
        self.start()
        pose = self.worker.recorded_pose(dict(self.bus.positions))
        kinematics = self.worker.retarget.kinematics
        def height(p):
            angles = self.worker.joint_mapping.urdf_angles(p,self.worker.limits,self.worker.config['raw_direction'])
            return kinematics.frames(angles)['gripper_frame_joint'][0]
        try: moved = self.worker.smart_nudge(pose,'up',2)
        except ValueError: moved = self.worker.smart_nudge(pose,'down',2)
        before,after = height(pose),height(moved)
        self.assertAlmostEqual(abs(after[2]-before[2]),.02,delta=.006)
        self.assertAlmostEqual(after[0],before[0],delta=.006)
        self.assertAlmostEqual(after[1],before[1],delta=.006)
        self.assertEqual(moved['wrist_roll'],pose['wrist_roll'])

    def test_turn_left_swings_the_tip_counter_clockwise(self):
        self.start()
        pose = self.worker.recorded_pose(dict(self.bus.positions))
        frames = self.worker.retarget.kinematics.frames
        def heading(p):
            angles = self.worker.joint_mapping.urdf_angles(p,self.worker.limits,self.worker.config['raw_direction'])
            found = frames(angles)
            end,base = found['gripper_frame_joint'][0],found['shoulder_pan'][0]
            return math.atan2(end[1]-base[1],end[0]-base[0])
        change = heading(self.worker.smart_turn(pose,10))-heading(pose)
        self.assertAlmostEqual((change+math.pi)%(2*math.pi)-math.pi,math.radians(10),delta=math.radians(1.5))


# The inherited plain-replay cases already run from test_replay.py.
for name in [n for n in dir(ControllerReplayTests) if n.startswith('test_')]:
    setattr(ControllerSmartTests,name,None)

if __name__=='__main__': unittest.main()
