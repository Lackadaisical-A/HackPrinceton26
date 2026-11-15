"""Smart replay: carries out one agent action at a time over a recording. No hardware access.

The agent (Gemini, in ../assist) chooses every action. This class turns one action into pose
targets for the ordinary replay writer, reports the outcome, and waits for the next action.
Recoverable trouble (a missed grasp, a stall, a dropped object) becomes a failed result the
agent can react to. Everything that protects the arm stays in robot_control/wearable.py.
"""
import math

from robot_control.reach import DIRECTIONS

ACTIONS = ('look','go_to_waypoint','play_segment','nudge','move_joints','grip','ask_operator','done')
ARM_JOINTS = ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll')
DEFAULTS = dict(max_actions=60,max_seconds=600.,decision_timeout_s=120.,max_failures=4,
                max_nudge_cm=5.,max_joint_deg=10.,max_pan_deg=25.,grip_timeout_s=5.,open_timeout_s=3.)


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def text(value,limit=200):
    return ' '.join(value.split())[:limit] if isinstance(value,str) else ''


class SmartReplay:
    def __init__(self,clip,arrival,tools,now,pose,config=None,run=None):
        """clip: a finished MotionReplay. arrival(pose,positions) -> object with update(measured,now,grasped).
        tools: nudge(pose,direction,cm), shift(pose,degrees_by_joint) and turn(pose,degrees_left), each
        returning a new pose."""
        self.clip,self.arrival,self.tools = clip,arrival,tools
        self.config = {**DEFAULTS,**(config or {})}
        self.run = run  # Names this run, so an answer meant for an earlier run is never taken.
        self.points = list(clip.points)
        self.open_gripper = max(frame['gripper'] for frame in clip.frames)
        self.closed_gripper = clip.limits['gripper'][0]
        self.started = self.since = now
        self.hold = dict(pose)
        self.state,self.seq,self.count,self.failures = 'waiting',1,0,0
        self.action = None
        self.result = dict(seq=0,ok=True,action=None,detail='Smart replay started. The arm is holding still.')
        self.detail = 'Waiting for Gemini to choose the first action.'
        self.stages,self.stage_started,self.arriving = [],now,None
        self.carrying = False
        self.clamped = False

    @property
    def finished(self): return self.state=='done'

    # --- Outcomes ---------------------------------------------------------------------------

    def report(self,ok,detail,now):
        self.result = dict(seq=self.seq,ok=ok,action=self.action['action'] if self.action else None,
                           detail=detail+(' The move was clamped to the joint limits.' if self.clamped else ''))
        self.count += 1
        self.failures = 0 if ok else self.failures+1
        self.seq += 1
        self.stages,self.arriving,self.clamped = [],None,False
        self.state,self.since = 'waiting',now
        self.detail = ('Done: ' if ok else 'Problem: ')+detail
        if self.count>=self.config['max_actions']:
            self.finish(f"Stopped after {self.count} actions (the limit).")
        elif self.failures>=self.config['max_failures']:
            self.state = 'asking'
            self.failures = 0
            self.detail = f"Gemini failed {self.config['max_failures']} times in a row. Last problem: {detail}"

    def finish(self,detail):
        self.state,self.detail = 'done',detail
        self.stages,self.arriving = [],None

    def resume(self,now):
        if self.state!='asking': raise ValueError('Smart replay is not waiting for the operator')
        self.report(True,'The operator said to continue.',now)

    # --- Accepting an action ----------------------------------------------------------------

    def clip_pose(self,pose):
        result = {}
        for name,value in pose.items():
            low,high = self.clip.limits[name]
            if not math.isfinite(value): raise ValueError('The move has an invalid joint value')
            if not low-1<=value<=high+1: self.clamped = True
            result[name] = max(low,min(high,value))
        return result

    def waypoint(self,value):
        if type(value) is not int or not 0<=value<len(self.points):
            raise ValueError(f'Waypoint must be a whole number from 0 to {len(self.points)-1}')
        return value

    def plan(self,message):
        """The cleaned action and its stages. Raises ValueError with a sentence the agent can act on."""
        kind = message.get('action')
        if kind not in ACTIONS: raise ValueError(f"Unknown action. Use one of: {', '.join(ACTIONS)}")
        action = dict(action=kind,why=text(message.get('why')))
        settle = ('settle',.25)
        if kind=='look':
            pan = message.get('pan') or 0
            if not number(pan): raise ValueError('look: pan must be a number of degrees')
            pan = max(-self.config['max_pan_deg'],min(self.config['max_pan_deg'],pan))
            action['pan'] = pan
            stages = ([('arrive',self.clip_pose(self.tools.turn(self.hold,pan)))] if pan else [])+[('settle',.4)]
        elif kind=='go_to_waypoint':
            index = action['waypoint'] = self.waypoint(message.get('waypoint'))
            # The agent owns the gripper between segments: travelling never opens or closes it.
            pose = {**self.clip.target_at(self.points[index]),'gripper':self.hold['gripper']}
            stages = [('arrive',pose),settle]
        elif kind=='play_segment':
            first = action['waypoint'] = self.waypoint(message.get('waypoint'))
            last = action['to'] = self.waypoint(message.get('to'))
            if last<=first: raise ValueError('play_segment: "to" must be a later waypoint than "waypoint"')
            start,end = self.points[first],self.points[last]
            stages = [('arrive',{**self.clip.target_at(start),'gripper':self.hold['gripper']}),
                      ('play',start,end),('arrive',self.clip.target_at(end))]
        elif kind=='nudge':
            direction,amount = message.get('direction'),message.get('amount')
            if direction not in DIRECTIONS: raise ValueError(f"nudge: direction must be one of {', '.join(DIRECTIONS)}")
            if not number(amount) or amount<=0: raise ValueError('nudge: amount must be a positive number of centimetres')
            action.update(direction=direction,amount=min(self.config['max_nudge_cm'],amount))
            stages = [('arrive',self.clip_pose(self.tools.nudge(self.hold,direction,action['amount']))),settle]
        elif kind=='move_joints':
            joints = message.get('joints')
            if not isinstance(joints,dict) or not joints or set(joints)-set(ARM_JOINTS) or not all(number(v) for v in joints.values()):
                raise ValueError(f"move_joints: joints must give degrees for some of {', '.join(ARM_JOINTS)}")
            limit = self.config['max_joint_deg']
            action['joints'] = {n:max(-limit,min(limit,v)) for n,v in joints.items()}
            stages = [('arrive',self.clip_pose(self.tools.shift(self.hold,action['joints']))),settle]
        elif kind=='grip':
            if message.get('grip') not in ('open','close'): raise ValueError('grip: grip must be "open" or "close"')
            action['grip'] = message['grip']
            stages = [(message['grip'],)]
        else:
            action['text'] = text(message.get('text'),300)
            stages = []
        return action,stages

    def submit(self,message,now):
        """Take the agent's answer to the turn we are waiting on. Anything else is ignored."""
        if (self.state!='waiting' or not isinstance(message,dict) or message.get('seq')!=self.seq
                or message.get('run')!=self.run): return False
        self.action = dict(action=message.get('action') if isinstance(message.get('action'),str) else None,
                           why=text(message.get('why')))
        try: self.action,self.stages = self.plan(message)
        except (ValueError,KeyError,TypeError) as error:
            self.report(False,str(error),now)
            return True
        kind = self.action['action']
        if kind=='done': self.finish(self.action['text'] or 'Gemini reports the task complete.')
        elif kind=='ask_operator':
            self.state = 'asking'
            self.detail = self.action['text'] or 'Gemini asks the operator to check the scene.'
        else:
            self.state,self.stage_started,self.arriving = 'acting',now,None
            self.detail = 'Acting: '+kind.replace('_',' ')
        return True

    # --- Every control tick -----------------------------------------------------------------

    def next_stage(self,now):
        self.stages.pop(0)
        self.stage_started,self.arriving = now,None

    def advance(self,now,positions,measured,grasp):
        stage = self.stages[0]
        kind = stage[0]
        if kind=='arrive':
            if self.arriving is None: self.arriving = self.arrival(stage[1],positions)
            if self.arriving.update(measured,now,grasp.contacted): self.next_stage(now)
            return dict(stage[1])
        if kind=='play':
            elapsed = stage[1]+now-self.stage_started
            if elapsed>=stage[2]: self.next_stage(now)
            return self.clip.target_at(min(elapsed,stage[2]))
        if kind=='settle':
            if now-self.stage_started>=stage[1]: self.next_stage(now)
            return dict(self.hold)
        if kind=='close':
            if grasp.holding: self.next_stage(now)
            elif grasp.closed_endpoint:
                raise ValueError('Closed on nothing: the gripper reached its closed end without touching an object.')
            elif now-self.stage_started>=self.config['grip_timeout_s']:
                raise ValueError('The gripper did not finish closing in time.')
            return {**self.hold,'gripper':self.closed_gripper}
        if abs(measured['gripper']-self.open_gripper)<=30 or now-self.stage_started>=self.config['open_timeout_s']:
            self.next_stage(now)
        return {**self.hold,'gripper':self.open_gripper}

    def step(self,now,pose,positions,measured,grasp):
        """The pose to command this tick. `pose` is the measured pose in recording units."""
        if self.state=='done': return dict(self.hold)
        if now-self.started>=self.config['max_seconds']:
            self.finish('Smart replay reached its time limit.')
            return dict(self.hold)
        dropped = self.carrying and not grasp.holding
        self.carrying = bool(grasp.holding)
        if self.state!='acting':
            if self.state=='waiting' and now-self.since>=self.config['decision_timeout_s']:
                self.finish('Gemini did not choose an action in time.')
            return dict(self.hold)
        kind = self.stages[0][0]
        try:
            # A recorded segment and an open let go on purpose; a close has nothing to drop yet.
            if dropped and kind not in ('play','open','close'):
                raise ValueError('Object dropped: the gripper lost its hold.')
            self.hold = self.advance(now,positions,measured,grasp)
        except ValueError as error:
            self.hold = dict(pose)
            self.report(False,str(error),now)
            return dict(self.hold)
        if not self.stages:
            done = self.action['action']
            self.report(True,'Holding an object.' if done=='grip' and self.action['grip']=='close'
                        else 'Gripper open.' if done=='grip'
                        else 'Finished '+done.replace('_',' ')+'.'+(' Holding an object.' if grasp.holding else ''),now)
        return dict(self.hold)

    def snapshot(self):
        return dict(run=self.run,state=self.state,seq=self.seq,action=self.action,result=self.result,count=self.count,
                    detail=self.detail,carrying=self.carrying)
