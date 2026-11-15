"""Stick tap/hold detection, saved motions and bounded replay for the wearable follower.

Pure logic: no serial port, servo bus or UI. WearableRobot's worker owns the arm.
"""
from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import time

from robot_control.arm import BODY,JOINTS


class StickButton:
    """Tap = press and release before hold_s. Hold fires once when a press reaches hold_s."""
    def __init__(self,hold_s=.8):
        self.hold_s = hold_s
        self.epoch = None
        self.reset()

    def reset(self):
        self.down_at = None   # Start time of the press being tracked.
        self.ready = False    # A released stick has been seen since startup/reconnect.
        self.used = False     # This press already produced a hold or was consumed.
        self.pressed = False  # Level of the latest fresh sample, whether or not it counts as a press.

    def consume(self):
        """The current press has done its job: no tap or hold will follow from it."""
        if self.down_at is not None: self.used = True

    def update(self,sample,now):
        sample = sample or {}
        seen = sample.get("seen")
        if not sample.get("sequence") or "pressed" not in sample \
                or not isinstance(seen,(int,float)) or not 0<=now-seen<=.3:
            self.reset()  # A press that spans a gap is never a tap.
            return None
        if sample.get("epoch")!=self.epoch:
            self.reset()
            self.epoch = sample.get("epoch")
        self.pressed = bool(sample["pressed"])
        if not self.pressed:
            event = "tap" if self.down_at is not None and not self.used else None
            self.down_at,self.used,self.ready = None,False,True
            return event
        if not self.ready: return None  # Held since startup/reconnect: wait for a release.
        if self.down_at is None:
            self.down_at = now
            return "press"
        if not self.used and now-self.down_at>=self.hold_s:
            self.used = True
            return "hold"
        return None


def valid_samples(samples):
    """Rows of [seconds, six raw servo positions]; time never goes backwards."""
    if not isinstance(samples,list) or len(samples)<2: raise ValueError("a motion needs at least two samples")
    previous = 0.
    for row in samples:
        if not isinstance(row,list) or len(row)!=len(JOINTS)+1 or not all(
                isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in row):
            raise ValueError("a motion sample must be seven finite numbers")
        if row[0]<previous or any(not 0<=v<=4095 for v in row[1:]):
            raise ValueError("a motion sample has a bad time or position")
        previous = row[0]
    return samples


def duration(samples):
    return samples[-1][0]-samples[0][0]


def save_motion(folder,samples):
    """Files sit directly in the folder: sensor.ps1 lists its subfolders as sensor sessions."""
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/time.strftime("motion_%Y%m%dT%H%M%SZ.json",time.gmtime())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version":1,"joints":list(JOINTS),"samples":valid_samples(samples)}))
    os.replace(temporary,path)
    return path


def load_latest(folder):
    """(samples, file name) for the newest saved motion, or (None, why not)."""
    files = sorted(Path(folder).glob("motion_*.json"))
    if not files: return None,"no saved motion"
    try:
        data = json.loads(files[-1].read_text())
        if data.get("version")!=1 or data.get("joints")!=list(JOINTS): raise ValueError("unknown motion format")
        return valid_samples(data["samples"]),files[-1].name
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        return None,f"{files[-1].name} ignored: {error}"


class Replay:
    """Follow a recorded motion inside the live controller's rate, lead and range bounds.

    rate is ticks/s and lead is ticks ahead of the measured position, per joint.
    """
    def __init__(self,samples,measured,limits,rate,lead):
        self.limits,self.rate,self.lead = limits,rate,lead
        self.times = [row[0]-samples[0][0] for row in samples]
        self.poses = [{n:min(limits[n][1],max(limits[n][0],v)) for n,v in zip(JOINTS,row[1:])} for row in samples]
        self.duration = self.times[-1]
        self.previous = dict(measured)  # Last commanded target for all six joints.
        self.phase,self.elapsed,self.stalled = "approach",0.,0.

    def pose(self,t):
        i = min(len(self.times)-2,max(0,bisect_right(self.times,t)-1))
        t0,t1 = self.times[i],self.times[i+1]
        f = 0. if t1<=t0 else min(1.,max(0.,(t-t0)/(t1-t0)))
        return {n:self.poses[i][n]+f*(self.poses[i+1][n]-self.poses[i][n]) for n in JOINTS}

    def step(self,measured,dt):
        """Next targets for all six joints, and whether the motion has finished."""
        if not math.isfinite(dt) or dt<0: raise ValueError("invalid control timing")
        dt = min(.1,dt)
        for name in JOINTS:
            low,high = self.limits[name]
            if not math.isfinite(measured[name]) or not low<=measured[name]<=high:
                raise ValueError(f"{name} outside calibrated range")
            if name in BODY and abs(self.previous[name]-measured[name])>max(self.lead[name]*2,1):
                raise ValueError(f"{name} tracking error; arm did not follow its previous target")
        before = self.elapsed
        if self.phase=="approach": goal = self.poses[0]
        else:
            # Advance only as far as the arm joints can follow, so they stay on the
            # recorded path. The gripper follows best-effort and never holds the arm up.
            look = min(dt,self.duration-self.elapsed)
            ahead = self.pose(self.elapsed+look)
            reach = min((self.rate[n]*dt/abs(ahead[n]-self.previous[n]) for n in BODY if ahead[n]!=self.previous[n]),
                        default=1)
            self.elapsed = min(self.duration,self.elapsed+look*min(1,reach))
            goal = self.pose(self.elapsed)
        target = {}
        for name in JOINTS:
            step = self.rate[name]*dt
            value = max(self.previous[name]-step,min(self.previous[name]+step,goal[name]))
            value = max(measured[name]-self.lead[name],min(measured[name]+self.lead[name],value))
            target[name] = max(self.limits[name][0],min(self.limits[name][1],value))
        changed = {n:abs(target[n]-self.previous[n]) for n in JOINTS}
        def arrived(name):
            # There, or as close as the lead bound allows while the servo has stopped moving.
            gap = abs(goal[name]-target[name])
            return gap<=.01 or (gap<=self.lead[name]/4 and changed[name]<=.5)
        self.previous = target
        progressed = self.elapsed-before>=dt/4 or any(v>.5 for v in changed.values())
        if self.phase=="approach":
            if all(arrived(n) for n in JOINTS):
                self.phase,progressed = "play",True
        elif self.elapsed>=self.duration and all(arrived(n) for n in BODY):
            return target,True
        self.stalled = 0. if progressed else self.stalled+dt
        if self.stalled>=3: raise ValueError("replay stalled; a joint cannot reach the recorded pose")
        return target,False
