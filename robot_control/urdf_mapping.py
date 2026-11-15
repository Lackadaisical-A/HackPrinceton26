"""Invert the user's LeRobot/RViz joint mapping using saved servo ranges.

Same range/offset/scale convention as Documents/lerobot/scripts_local/
rviz_udp_lerobot_bridge.py and real_state_udp_receiver.py. No hardware writes.
"""
import json
import math
from pathlib import Path

from robot_control.arm import BODY

TICKS_PER_DEGREE = 4095 / 360


class JointMapping:
    def __init__(self,urdf_limits,path=Path(__file__).with_name("joint_mapping.json"),trim_deg=None):
        self.urdf_limits = urdf_limits
        self.config = json.loads(Path(path).read_text())
        trim_deg = {} if trim_deg is None else trim_deg
        if not isinstance(trim_deg,dict) or set(trim_deg)-set(BODY):
            raise ValueError('Servo trim must name an arm joint')
        self.trim_deg = {name:trim_deg.get(name,0.) for name in BODY}
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>45
               for v in self.trim_deg.values()):
            raise ValueError('Servo trim must be within -45..45 physical degrees')
        for name in BODY:
            low,high = urdf_limits[name]
            scale = self.config['urdf_scales'][name]
            offset = self.config['urdf_offsets_deg'][name]
            if not all(math.isfinite(v) for v in (low,high,scale,offset)) or high<=low or scale<=0:
                raise ValueError(f"Invalid physical URDF mapping: {name}")
            if not isinstance(self.config['joint_flips'][name],bool):
                raise ValueError(f"Invalid joint direction: {name}")

    def raw_targets(self,source,raw_limits,direction):
        result = {}
        for name in BODY:
            angle = source[name]
            if not math.isfinite(angle): raise ValueError(f"Invalid URDF target: {name}")
            intercept,slope = self.affine(name,raw_limits,direction)
            # Leave unclipped here so diagnostics can show saturation. The final
            # motion limiter clamps to saved hardware bounds before every write.
            # Trim is in physical encoder degrees, independent of URDF range
            # scaling and direction switches. +wrist_roll is CCW viewed from
            # behind the wrist looking toward the claw (this arm's mounting).
            result[name] = intercept+slope*angle
        return result

    def affine(self,name,raw_limits,direction):
        minimum,maximum = raw_limits[name]
        if not 0<=minimum<maximum<=4095: raise ValueError(f'Invalid servo limits: {name}')
        low,high = self.urdf_limits[name]
        offset = math.radians(self.config['urdf_offsets_deg'][name])
        sign = direction[name]*(-1 if self.config['joint_flips'][name] else 1)
        slope = sign*(maximum-minimum)/(self.config['urdf_scales'][name]*(high-low))
        intercept = (minimum if sign>0 else maximum)-slope*(low+offset)
        if name=='wrist_roll':
            # Keep the registered center, but use real encoder degrees for
            # roll. The old 360/320 range scaling would turn a requested 2x
            # human wrist gain into 2.25x at the physical motor.
            reference = (low+high)/2
            anchor = intercept+slope*reference
            slope = sign*TICKS_PER_DEGREE*180/math.pi
            intercept = anchor-slope*reference
        return intercept+self.trim_deg[name]*TICKS_PER_DEGREE,slope

    def urdf_angles(self,raw,raw_limits,direction):
        """Inverse of raw_targets, including physical trim; radians, unclipped.

        Comparing these values to the preview isolates motor lag from mapping
        errors. Rendering may clip angles, but diagnostics must keep the error.
        """
        result = {}
        for name in BODY:
            if not math.isfinite(raw[name]):
                raise ValueError(f'Invalid measured servo mapping: {name}')
            intercept,slope = self.affine(name,raw_limits,direction)
            result[name] = (raw[name]-intercept)/slope
        return result

    def degree_gains(self,raw_limits,direction):
        """Signed physical motor degrees per degree of preview joint movement."""
        result = {}
        for name in BODY:
            _,slope = self.affine(name,raw_limits,direction)
            result[name] = slope*math.pi/180/TICKS_PER_DEGREE
        return result
