"""Validated, session-only host tuning; servo firmware settings stay unchanged."""
from copy import deepcopy
import math

from robot_control.joints import BODY

RANGES = {'speed_deg_s':(10.,360.), 'response_ms':(1.,100.), 'control_hz':(20.,100.)}
LEAD_RANGE = (1.,45.)
CONTROL_RANGES = {'speed_deg_s':RANGES['speed_deg_s'], 'claw_speed_per_s':(.1,3.),
                  'max_load':(1.,1023.), 'gripper_max_load':(1.,1023.),
                  'gripper_contact_load':(20.,350.), 'gripper_hold_load':(10.,300.)}
CONTROL_DEFAULTS = {'gripper_max_load':350,'gripper_contact_load':180,'gripper_hold_load':120}


def control_values(config):
    return {**{key:config.get(key,CONTROL_DEFAULTS.get(key)) for key in CONTROL_RANGES},
            'load_stops_enabled':config.get('load_stops_enabled',False)}

# Named, repeatable host-command tradeoffs. These are UI choices, not claims
# about measured positioning accuracy or changes to servo PID/torque settings.
RESPONSE_PROFILES = (
    ('Precise','Slower, steadier movement. More smoothing, with more delay.',30.,65.,2.,4.),
    ('Controlled','Gentle movement with less delay than Precise.',45.,50.,2.5,4.5),
    ('Balanced','Moderate response with room for loaded joints to catch up.',120.,20.,12.,18.),
    ('Quick','Fast following with a short response filter.',180.,10.,20.,30.),
    ('Fast','Default: match the preview promptly, with minimal filtering.',240.,5.,30.,40.),
)


def response_profile(index):
    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(RESPONSE_PROFILES):
        raise ValueError('Choose a response profile from Precise to Fast')
    _,_,speed,smoothing,lead,loaded_lead = RESPONSE_PROFILES[index]
    return dict(speed_deg_s=speed,response_ms=smoothing,control_hz=100.,
                joint_lead_deg={name:loaded_lead if name in ('shoulder_lift','elbow_flex') else lead for name in BODY})


def response_profile_index(values):
    """Report custom settings honestly, including a custom control frequency."""
    return next((i for i in range(len(RESPONSE_PROFILES)) if values==response_profile(i)),None)


def lead_degrees(config,name):
    return config['joint_lead_deg'].get(name,config['max_lead_deg'])


def tuning_values(config):
    return {**{key:config[key] for key in RANGES},
            'joint_lead_deg':{name:lead_degrees(config,name) for name in BODY}}


def checked_number(value,limits,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    low,high = limits
    if not low<=value<=high: raise ValueError(f'{label} must be {low:g}–{high:g}')
    return float(value)


def apply_values(config,changes):
    if not isinstance(changes,dict) or set(changes)-set(RANGES)-set(CONTROL_RANGES)-{'joint_lead_deg','load_stops_enabled'}:
        raise ValueError('Unknown live tuning setting')
    result = deepcopy(config)
    if 'load_stops_enabled' in changes:
        if type(changes['load_stops_enabled']) is not bool: raise ValueError('Load stops must be on or off')
        result['load_stops_enabled'] = changes['load_stops_enabled']
    for key,limits in {**RANGES,**CONTROL_RANGES}.items():
        if key in changes: result[key] = checked_number(changes[key],limits,key)
    if result.get('gripper_hold_load',120)>result.get('gripper_contact_load',180):
        raise ValueError('Grip holding load must not exceed contact load')
    if 'joint_lead_deg' in changes:
        leads = changes['joint_lead_deg']
        if not isinstance(leads,dict) or set(leads)-set(BODY): raise ValueError('Unknown arm joint in live tuning')
        for name,value in leads.items():
            result['joint_lead_deg'][name] = checked_number(value,LEAD_RANGE,name+' lead')
    return result
