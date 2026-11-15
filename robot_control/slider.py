"""Joystick-X velocity control on the existing arm worker's serial connection."""
import json
import math
from dataclasses import replace
from pathlib import Path

from robot_control.arm import MotorCalibration

CALIBRATION = Path(__file__).resolve().parents[1]/'leslider_control/calibration/robots/so101_slider_follower/my_so101_follower.json'


def slider_position_from_raw(raw,calibration):
    if not math.isfinite(raw) or not calibration.range_min<=raw<=calibration.range_max:
        raise ValueError('Slider left its referenced rail range; reference again')
    fraction = (raw-calibration.range_min)/(calibration.range_max-calibration.range_min)
    return 100*(1-fraction if calibration.drive_mode else fraction)


class JoystickSlider:
    def __init__(self, joystick, path=CALIBRATION):
        self.joystick = joystick
        self.calibration = MotorCalibration(**json.loads(path.read_text())['slider'])
        # Same version-4 Phase-28 metadata as the existing LeSlider controller.
        metadata = json.loads(path.with_name(path.stem+'.slider_safety.json').read_text())
        expected = dict(version=4,phase=28,slider_id=self.calibration.id,drive_mode=self.calibration.drive_mode,
                        range_min=self.calibration.range_min,range_max=self.calibration.range_max,
                        travel_ticks=self.calibration.range_max-self.calibration.range_min)
        if any(metadata.get(key)!=value for key,value in expected.items()): raise ValueError('Slider calibration metadata mismatch')
        if expected['travel_ticks']<4096 or self.calibration.drive_mode not in (0,1): raise ValueError('Invalid saved slider travel')
        if self.calibration.id!=7: raise ValueError('Slider calibration must use ID 7')
        self.bus = None
        self.reference = None
        self.enabled = False
        self.position = self.target = None
        self.message = 'Release slider, place at an endpoint, then set its reference.'
        self.speed = 600.  # Existing LeSlider tester's maximum raw ticks/s.
        self.lead = 320.  # Existing bounded probe allowance for static friction.
        self.last_raw = self.previous = None
        self.progress_at = 0.
        self.progress_raw = None
        self.wrong_since = None
        self.command_direction = 0
        self.direction_origin = None

    def read(self,name):
        return self.bus.read(name,'slider',normalize=False,num_retry=0)

    def write(self,name,value):
        self.bus.write(name,'slider',value,normalize=False,num_retry=0)

    def attach(self,arm):
        was_attached = self.bus is not None
        if not was_attached: self.bus = arm.slider_bus()
        expected = {'Operating_Mode':0,'Phase':28,'Homing_Offset':0,'Min_Position_Limit':0,'Max_Position_Limit':0}
        try:
            for key,value in expected.items():
                if self.read(key)!=value:
                    raise ValueError(f'Slider {key} must be {value}; run the existing slider setup first.')
        except Exception:
            self.reference = None
            if not was_attached: self.bus = None
            raise

    def release(self,arm):
        self.attach(arm)
        self.stop('Slider released. Place at 0% or 100%, then mark that endpoint.')
        self.reference = None
        self.position = self.target = None

    def set_reference(self,arm,endpoint):
        self.attach(arm)
        self.stop('Referencing slider')
        self.reference = None
        raw = self.read('Present_Position')
        if endpoint not in (0,100): raise ValueError('Choose endpoint 0 or 100')
        direction = -1 if self.calibration.drive_mode else 1
        travel = self.calibration.range_max-self.calibration.range_min
        zero = raw-direction*(endpoint/100)*travel
        hundred = zero+direction*travel
        low,high = min(zero,hundred),max(zero,hundred)
        if not math.isfinite(raw) or low < -30719 or high > 30719: raise ValueError('Slider session exceeds multi-turn counter range')
        self.reference = replace(self.calibration,range_min=round(low),range_max=round(high))
        self.previous = self.last_raw = raw
        self.position = self.target = float(endpoint)
        self.message = f'Slider referenced at {endpoint}%. Center X, then enable.'

    def enable(self,arm,sample,now,before_write=lambda:None):
        self.attach(arm)
        if self.reference is None: raise ValueError('Reference the slider at 0% or 100% first')
        if self.joystick.axis(sample['x'],'x')!=0: raise ValueError('Center joystick X before enabling the slider')
        raw = self.read('Present_Position')
        self.position = slider_position_from_raw(raw,self.reference)
        self.previous = self.last_raw = self.progress_raw = raw
        self.progress_at = now
        self.wrong_since = None
        self.command_direction = 0
        self.direction_origin = raw
        # Latch the measured position before enabling, never a saved-session target.
        before_write()
        self.write('Goal_Position',round(raw))
        self.write('Goal_Time',0)
        self.write('Goal_Velocity',round(self.speed))
        before_write()
        self.write('Torque_Enable',1)
        self.enabled = True
        self.message = 'Joystick X controls slider speed; center holds.'

    def stop(self,reason):
        self.enabled = False
        self.message = reason
        if self.bus is not None:
            # A horizontal rail can stop by releasing ID 7; no stale goal re-enable.
            self.write('Torque_Enable',0)
        self.target = self.position

    def raw_target(self,percent):
        if self.reference is None: raise ValueError('Reference the slider before replay')
        if not math.isfinite(percent) or not 0<=percent<=100: raise ValueError('Invalid recorded slider position')
        fraction = 1-percent/100 if self.reference.drive_mode else percent/100
        return self.reference.range_min+fraction*(self.reference.range_max-self.reference.range_min)

    def step(self,sample,dt,now,before_write=lambda:None,target_raw=None):
        if not self.enabled: return
        if not math.isfinite(dt) or not 0<=dt<.25: raise ValueError('Slider control timing stale')
        age = now-sample['seen']
        if not 0<=age<.3 or sample['pressed']: raise ValueError('Slider joystick stale or stop pressed')
        raw = self.read('Present_Position')
        try: self.position = slider_position_from_raw(raw,self.reference)
        except ValueError:
            self.reference = None
            raise
        if self.read('Torque_Enable')!=1:
            self.reference = None
            raise ValueError('Slider torque switched off; reference again')
        if self.last_raw is not None and abs(raw-self.last_raw)>max(100,self.speed*dt*3):
            self.reference = None
            raise ValueError('Slider position jumped; reference again')
        axis = self.joystick.axis(sample['x'],'x')
        direction = -1 if self.reference.drive_mode else 1
        velocity = direction*axis*self.speed
        if target_raw is not None:
            slider_position_from_raw(target_raw,self.reference)
            velocity = max(-self.speed,min(self.speed,(target_raw-raw)/max(dt,.001)))
            axis = velocity/self.speed
        # Detect measured movement opposite the preceding commanded direction.
        reverse_progress = max(0,-self.command_direction*(raw-self.direction_origin))
        if reverse_progress>=12:
            self.wrong_since = now if self.wrong_since is None else self.wrong_since
            if reverse_progress>=48 or now-self.wrong_since>=.12:
                raise ValueError('Slider moved in the wrong direction')
        else: self.wrong_since = None
        if abs(raw-self.progress_raw)>=8 or axis==0:
            self.progress_at,self.progress_raw = now,raw
        elif now-self.progress_at>1.75 and abs(self.previous-raw)>30:
            raise ValueError('Slider stalled')
        # Center and reversals discard the previous lead so release responds now.
        next_direction = (velocity>0)-(velocity<0)
        changed_direction = next_direction!=self.command_direction
        start = raw if changed_direction else self.previous
        if changed_direction:
            self.direction_origin = raw
            self.wrong_since = None
        self.command_direction = next_direction
        step = self.speed*min(.1,dt)
        target = start+velocity*min(.1,dt) if target_raw is None else start+max(-step,min(step,target_raw-start))
        target = max(raw-self.lead,min(raw+self.lead,target))
        target = max(self.reference.range_min,min(self.reference.range_max,target))
        if self.read('Phase')!=28:
            self.reference = None
            raise ValueError('Slider multi-turn mode changed; reference again')
        before_write()
        self.write('Goal_Position',round(target))
        self.previous,self.last_raw = target,raw
        self.target = slider_position_from_raw(target,self.reference)

    def snapshot(self):
        return dict(referenced=self.reference is not None,enabled=self.enabled,pos=self.position,target=self.target,detail=self.message)
