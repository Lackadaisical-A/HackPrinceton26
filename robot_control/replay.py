"""Button gestures and a bounded, measured-pose recording. No hardware access."""
from bisect import bisect_right
import math

DOUBLE_CLICK_S = .35
HOLD_STOP_S = .7


class ClickGestures:
    def __init__(self): self.reset()

    def reset(self):
        self.epoch = None
        self.raw = self.down = False
        self.changed = self.pressed_at = 0.
        self.first = None
        self.suppressed = True
        self.long_sent = False
        self.second = False
        self.just_pressed = False

    def held(self,now):
        return self.down and not self.suppressed and now-self.pressed_at>=HOLD_STOP_S

    def update(self,pressed,epoch,now):
        self.just_pressed = False
        if self.epoch!=epoch:
            self.reset()
            self.epoch = epoch
            self.raw = self.down = bool(pressed)
            self.changed = self.pressed_at = now
            self.suppressed = bool(pressed)
            return None
        if bool(pressed)!=self.raw:
            self.raw,self.changed = bool(pressed),now
        if self.raw!=self.down and now-self.changed>=.02:
            self.down = self.raw
            if self.down:
                self.pressed_at = now
                self.just_pressed = not self.suppressed
                self.long_sent = False
                self.second = self.first is not None and now-self.first<=DOUBLE_CLICK_S
            elif self.suppressed or self.long_sent:
                self.suppressed = False
                self.first = None
            elif self.second:
                self.first = None
                return 'record'
            else:
                self.first = now
        if self.held(now) and not self.long_sent:
            self.long_sent = True
            self.first = None
            return 'stop'
        if self.first is not None and not self.down and not self.raw and now-self.first>DOUBLE_CLICK_S:
            self.first = None
            return 'play'
        return None


class PoseArrival:
    def __init__(self,target,measured,now,tolerances,config):
        self.target,self.tolerances,self.config = target,tolerances,config
        self.started,self.settled = now,None
        self.best = {n:abs(target[n]-measured[n]) for n in target}
        self.progress = dict.fromkeys(target,now)

    def update(self,measured,now,grasped=False):
        errors = {n:0 if n=='gripper' and grasped else abs(v-measured[n]) for n,v in self.target.items()}
        if all(errors[n]<=self.tolerances[n] for n in errors):
            if self.settled is None: self.settled = now
            if now-self.settled>=self.config['alignment_settle_s']: return True
        else: self.settled = None
        for name,error in errors.items():
            if error<self.best[name]-2 or error<=self.tolerances[name]:
                self.best[name],self.progress[name] = error,now
            if now-self.progress[name]>=self.config['alignment_stall_s']:
                raise ValueError(f'Replay positioning stalled: {name}')
        if now-self.started>=self.config['alignment_timeout_s']:
            raise ValueError('Replay positioning timed out')
        return False


class MotionReplay:
    MAX_SECONDS = 120.
    SAMPLE_PERIOD = .02
    MAX_EVENTS = 40
    MAX_WAYPOINTS = 32

    def __init__(self):
        self.frames = []
        self.times = []
        self.phase = 'empty'
        self.revision = 0
        self.started = self.elapsed = 0.
        self.limits = {}
        self.detail = 'Double-click to record; single-click to replay.'
        self.request = None
        self.events = []
        self.points = []
        self.recorded_wall = None
        self.holding = False
        self.agent = None

    @property
    def busy(self): return self.phase in ('returning','playing','finishing','smart')

    @property
    def duration(self): return self.times[-1] if self.times else 0.

    @property
    def has_slider(self): return 'slider' in self.limits

    def validate(self,pose,limits=None):
        limits = self.limits if limits is None else limits
        if set(pose)!=set(limits): raise ValueError('Recording channels changed')
        if any(not math.isfinite(pose[n]) or not lo<=pose[n]<=hi for n,(lo,hi) in limits.items()):
            raise ValueError('Recording pose is outside calibrated travel')

    def record(self,pose,limits,now,wall=None):
        if self.busy: raise ValueError('Stop replay before starting a new recording')
        self.validate(pose,limits)
        self.limits = dict(limits)
        self.frames,self.times = [dict(pose)],[0.]
        self.events,self.points,self.holding,self.agent = [],[],False,None
        self.recorded_wall = wall
        self.started,self.elapsed = now,0.
        self.phase = 'recording'
        self.revision += 1
        self.detail = 'Recording measured robot motion. Double-click again to finish.'

    def sample(self,pose,now,force=False):
        if self.phase!='recording': return
        self.validate(pose)
        elapsed = min(self.MAX_SECONDS,max(0.,now-self.started))
        if elapsed>self.duration and (force or elapsed-self.duration>=self.SAMPLE_PERIOD):
            self.frames.append(dict(pose))
            self.times.append(elapsed)
        self.elapsed = elapsed
        if elapsed>=self.MAX_SECONDS: self.stop('Recording reached the 120-second limit.')

    def note_holding(self,holding,now):
        """Mark a grasp or release on the timeline when the inferred hold changes during a recording."""
        if self.phase!='recording' or bool(holding)==self.holding: return
        self.holding = bool(holding)
        if len(self.events)<self.MAX_EVENTS:
            t = min(self.MAX_SECONDS,max(0.,now-self.started))
            self.events.append(dict(t=round(t,3),kind='grasp' if holding else 'release'))

    def waypoints(self):
        """Times of the poses an agent may name: start, end, every grasp/release, and an even fill between."""
        if len(self.frames)<2: return []
        fixed = sorted({0.,round(self.duration,3),*(min(e['t'],round(self.duration,3)) for e in self.events)})
        room = max(0,self.MAX_WAYPOINTS-len(fixed))
        step = max(.75,self.duration/(room+1))
        fill = [round(i*step,3) for i in range(1,int(self.duration/step)+1)]
        fill = [t for t in fill if all(abs(t-f)>=.3 for f in fixed)][:room]
        return sorted(fixed+fill)

    def stop(self,detail='Stopped.'):
        if self.phase=='recording': self.points = self.waypoints()
        self.phase = 'ready' if len(self.frames)>=2 and self.duration>=.1 else 'empty'
        self.revision += 1
        self.detail = detail

    def play(self,now):
        if self.phase!='ready': raise ValueError('Finish a recording of at least 0.1 seconds before replaying')
        self.phase,self.started,self.elapsed = 'returning',now,0.
        self.revision += 1
        self.detail = 'Returning to the recorded first pose.'

    def play_smart(self,now):
        if self.phase!='ready': raise ValueError('Finish a recording of at least 0.1 seconds before smart replay')
        self.phase,self.started,self.elapsed = 'smart',now,0.
        self.revision += 1
        self.detail = 'Smart replay: Gemini chooses each action.'

    def start_timeline(self,now):
        self.phase,self.started,self.elapsed = 'playing',now,0.
        self.detail = 'Replaying recorded motion.'
        self.revision += 1

    def target(self,now):
        if self.phase=='returning': return dict(self.frames[0])
        self.elapsed = min(self.duration,max(0.,now-self.started))
        return self.target_at(self.elapsed)

    def target_at(self,elapsed):
        """The recorded pose at a time on the recording's own clock, interpolated between samples."""
        elapsed = min(self.duration,max(0.,elapsed))
        index = bisect_right(self.times,elapsed)
        if index>=len(self.times): return dict(self.frames[-1])
        left = max(0,index-1)
        ratio = (elapsed-self.times[left])/(self.times[index]-self.times[left])
        return {n:a+ratio*(self.frames[index][n]-a) for n,a in self.frames[left].items()}

    def snapshot(self):
        return dict(phase=self.phase,revision=self.revision,duration_s=round(self.duration,3),
                    elapsed_s=round(self.elapsed,3),samples=len(self.frames),includes_slider=self.has_slider,
                    detail=self.detail,request=self.request,events=[dict(e) for e in self.events],
                    waypoints=list(self.points),recorded_wall=self.recorded_wall,agent=self.agent)
