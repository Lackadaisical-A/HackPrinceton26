"""Physical SO-101 follower for the calibrated Wearable Lab geometry.

Only the worker thread owns COM6. UI updates replace a single latest input;
there is no motion queue. Connecting is read-only; motion needs manual enable.
"""
from copy import deepcopy
from collections import deque
import json
import logging
import math
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace

from robot_control.arm import Arm,BODY,JOINTS
from robot_control.joystick import JoystickClaw
from robot_control.slider import JoystickSlider
from robot_control.replay import MotionReplay,ClickGestures,PoseArrival
from robot_control.smart_replay import SmartReplay
from robot_control.reach import direction_vector,step_joints
from robot_control.urdf_mapping import JointMapping
from robot_control.alignment import Alignment
from robot_control.tuning import apply_values,tuning_values,control_values,lead_degrees,RANGES,LEAD_RANGE
from wearable_test.retarget import Retargeter
from wearable_test.io import fresh_frame

HERE = Path(__file__).resolve().parent
URDF = HERE.parent/"so101_sim/model/so101_new_calib.urdf"
TICKS_PER_RADIAN = 4095/(2*math.pi)  # Matches the vendored LeRobot degree convention.


def input_packet(geometry,snapshot,valid,reason,revision,reverse,now):
    joystick = snapshot["wroom"]["joystick"]
    joystick = {**(joystick or {}),"sequence":int(joystick is not None),"epoch":snapshot["wroom"]["epoch"]}
    reverse = {name:bool(reverse.get(name,False)) for name in JOINTS}
    samples = [sample for board in snapshot.values() for sample in board["samples"].values()]
    return dict(geometry=geometry,joystick=joystick,reverse=reverse,
                epoch=(snapshot["wroom"]["epoch"],snapshot["s3"]["epoch"],revision,*reverse.values()),
                seen=min(sample["seen"] for sample in samples),published=now,
                valid=bool(valid and all(board["connected"] for board in snapshot.values())),reason=reason)


def load_config(path=HERE/"wearable_config.json"):
    config = json.loads(Path(path).read_text())
    for name,upper in (("speed_deg_s",RANGES['speed_deg_s'][1]),("alignment_deg_s",120),("control_hz",100),("response_ms",100),
                       ("max_lead_deg",LEAD_RANGE[1]),("max_load",1023),('alignment_tolerance_deg',5),
                       ('alignment_settle_s',2),('alignment_stall_s',10),('alignment_timeout_s',45)):
        if isinstance(config[name],bool) or not isinstance(config[name],(int,float)) or not math.isfinite(config[name]) or not 0<config[name]<=upper:
            raise ValueError(f"Invalid physical robot setting: {name}")
    if config["control_hz"]<20: raise ValueError("control_hz must be 20..100")
    if config['mapping_mode'] not in ('absolute','relative'): raise ValueError('Invalid mapping_mode')
    if not config['alignment_settle_s']<config['alignment_stall_s']<config['alignment_timeout_s']:
        raise ValueError('Alignment times must satisfy settle < stall < timeout')
    for name,lead in config['joint_lead_deg'].items():
        if name not in BODY or isinstance(lead,bool) or not isinstance(lead,(int,float)) or not math.isfinite(lead) or not 0<lead<=LEAD_RANGE[1]:
            raise ValueError(f'Invalid joint lead bound: {name}')
    if set(config["raw_direction"])!=set(BODY) or any(v not in (-1,1) for v in config["raw_direction"].values()):
        raise ValueError("raw_direction must contain +1 or -1 for each arm joint")
    apply_values(config,tuning_values(config))  # Startup and live bounds agree.
    apply_values(config,control_values(config))
    JoystickClaw(speed=config['claw_speed_per_s'],max_lead=config['claw_lead_fraction'])
    return config


def bounded_follow(anchor,source_anchor,source,previous,measured,limits,dt,config,limited=None):
    """Anchor at enable, then follow URDF-angle differences without a position jump."""
    desired = {n:anchor[n]+config['raw_direction'][n]*(source[n]-source_anchor[n])*TICKS_PER_RADIAN for n in BODY}
    return bounded_targets(desired,previous,measured,limits,dt,config,limited)


def bounded_targets(desired,previous,measured,limits,dt,config,limited=None,flags=None,previous_config=None):
    """Smooth raw targets, enforcing speed, measured lead and saved joint limits."""
    if not math.isfinite(dt) or dt<0: raise ValueError("invalid control timing")
    step = math.radians(config["speed_deg_s"])*TICKS_PER_RADIAN*min(.1,dt)
    alpha = -math.expm1(-min(.1,dt)/(config["response_ms"]/1000))
    result = {}
    for name in BODY:
        lead = math.radians(config['joint_lead_deg'].get(name,config['max_lead_deg']))*TICKS_PER_RADIAN
        values = (desired[name],previous[name],measured[name],*limits[name])
        if not all(math.isfinite(v) for v in values): raise ValueError(f"invalid {name} telemetry")
        low,high = limits[name]
        if not low<=measured[name]<=high: raise ValueError(f"{name} outside calibrated range")
        tracking_lead = math.radians(lead_degrees(previous_config,name))*TICKS_PER_RADIAN if previous_config else lead
        if abs(previous[name]-measured[name])>max(tracking_lead*2,1):
            raise ValueError(f"{name} tracking error; arm did not follow its previous target")
        target = desired[name]
        reasons = []
        if not low<=target<=high: reasons.append('RANGE')
        if limited is not None and not low<=target<=high: limited.append(name)
        target = max(low,min(high,target))
        # The default 5 ms filter adds little lag to the shared preview solution.
        target = previous[name]+alpha*(target-previous[name])
        if abs(target-previous[name])>step: reasons.append('RATE')
        target = max(previous[name]-step,min(previous[name]+step,target))
        if abs(target-measured[name])>lead: reasons.append('LEAD')
        target = max(measured[name]-lead,min(measured[name]+lead,target))
        result[name] = max(low,min(high,target))
        if flags is not None: flags[name] = ' + '.join(reasons)
    return result


class WearableRobot:
    def __init__(self,port,joystick_calibration,arm_factory=Arm,clock=time.monotonic,config=None,timer=time.perf_counter,telemetry_endpoint=None):
        self.telemetry_endpoint = telemetry_endpoint
        self.telemetry = None
        self.haptics = None
        self.auto_enable_claw = True  # Arm enable also starts claw control and haptic feedback.
        self.port,self.arm_factory,self.clock = port,arm_factory,clock
        # Packet age shares the sensors' monotonic clock. Durations and servo
        # slew use perf_counter: Windows Python 3.12 monotonic ticks at 15.625 ms.
        self.timer = timer
        self.config = deepcopy(config) if config is not None else load_config()
        self.startup_tuning = tuning_values(self.config)
        self.pending_config = None
        self.tuning_requested = self.tuning_applied = 0
        self.claw = JoystickClaw(calibration=joystick_calibration,speed=self.config['claw_speed_per_s'],
                                 max_lead=self.config['claw_lead_fraction'])
        self.claw.load_limit = control_values(self.config)['gripper_max_load']
        self.claw.load_stops_enabled = self.config.get('load_stops_enabled',False)
        self.claw.grasp.contact_load = control_values(self.config)['gripper_contact_load']
        self.claw.grasp.hold_load = control_values(self.config)['gripper_hold_load']
        self.grip_hold_active = False
        self.slider = JoystickSlider(joystick_calibration)
        self.replay = MotionReplay()
        self.smart = None  # SmartReplay while Gemini drives a recording; kept afterwards for its last status.
        self.agent_inbox = None  # Set by the app: latest() gives the agent's newest action message.
        self.clicks = ClickGestures()
        self.retarget = Retargeter(URDF)
        self.joint_mapping = JointMapping(self.retarget.limits,trim_deg=self.config['servo_trim_deg'])
        self.aligning = False
        self.alignment = None
        self.mapping_epoch = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.commands = queue.Queue()
        self.packet = None
        self.source_hub = None
        self.reference_state = None
        self.generation = 0
        self.pause_pending = None
        self.enabled = False
        self.active_epoch = None
        self.intervals = deque(maxlen=50)
        self.next_timing_log = 0.
        self.status = dict(connected=False,enabled=False,claw_enabled=False,can_enable=False,
                           message=f"Connecting to {port} (read only)",seen=None,positions={},loads={},torque={},targets={},
                           loop_hz=0.,read_ms=0.,limited=[],input_status="Waiting for wearable readings",
                           desired={},flags={},aligning=False,alignment_progress='',matching={})
        self.status.update(tuning=tuning_values(self.config),controls=control_values(self.config),tuning_applied=0,tuning_requested=0,
                           slider=self.slider.snapshot(),replay=self.replay.snapshot(),grasp=self.claw.grasp.snapshot(),grip_hold_active=False,
                           enable_request=None)
        self.thread = threading.Thread(target=self.run,daemon=True,name="Physical SO101 wearable controller")

    def start(self): self.thread.start()

    def reconnect(self):
        """Restart only a stopped serial worker; never replay motion commands."""
        with self.lock:
            if self.closed.is_set(): raise ValueError('Controller is closed')
            if self.status['connected'] or self.thread.is_alive(): raise ValueError('Robot worker is still connected or finishing shutdown')
            self.generation += 1
            self.commands = queue.Queue()
            self.pause_pending = None
            self.enabled = self.aligning = False
            self.alignment = None
            self.claw.pause('Reconnect requires manual enable')
            self.grip_hold_active = False
            self.replay.stop('Reconnected. Recording kept; enable or replay explicitly.')
            self.clicks.reset()
            self.slider = JoystickSlider(self.claw.calibration)
            self.status.update(connected=False,enabled=False,claw_enabled=False,can_enable=False,
                               seen=None,loads={},positions={},targets={},aligning=False,alignment_progress='',
                               slider=self.slider.snapshot(),replay=self.replay.snapshot(),grasp=self.claw.grasp.snapshot(),grip_hold_active=False,message=f'Reconnecting {self.port} (read only)...')
            self.thread = threading.Thread(target=self.run,daemon=True,name='Physical SO101 wearable controller')
            self.thread.start()

    def connect_arm(self,arm):
        """Bounded read-only retries for power-up/USB timing, with no firmware bypass."""
        for attempt in range(3):
            if self.closed.is_set(): return False
            try:
                arm.connect()
                return True
            except (ConnectionError,OSError,RuntimeError) as error:
                arm.close()  # Also releases a port opened before a failed handshake.
                message = str(error)
                retryable = isinstance(error,(ConnectionError,OSError)) or (
                    'Missing motor IDs:' in message and 'Motors with incorrect model numbers:' not in message)
                if not retryable or attempt==2: raise
                with self.lock:
                    self.generation += 1
                    self.commands = queue.Queue()
                self.note(f'{self.port}: motors not responding. Check motor power and bus cable. Retrying connection ({attempt+2}/3)...')
                if self.closed.wait(.5): return False
        return False

    def submit(self,packet):
        with self.lock: self.packet = deepcopy(packet)

    def bind_hub(self,hub):
        """Use the existing serial owners; never open ESP32 ports a second time."""
        self.source_hub = hub
        hub.button_stops_feedback = False  # Short clicks now control record/replay; hold/Stop still stops haptics.

    def submit_reference(self,model,snapshot,valid,reason,revision,reverse,now):
        # Tk only publishes immutable calibration/control state. Geometry and
        # fresh sensor reads are computed by the physical worker at its rate.
        state = dict(model=deepcopy(model),epochs=tuple(snapshot[n]['epoch'] for n in ('wroom','s3')),
                     valid=valid,reason=reason,revision=revision,reverse=dict(reverse),ui_seen=now)
        with self.lock: self.reference_state = state

    def refresh_input(self):
        if self.source_hub is None: return
        with self.lock: state = self.reference_state
        if state is None: return
        snapshot = self.source_hub.snapshot()
        now = self.clock()
        valid,reason,geometry = False,state['reason'],None
        try:
            if not state['valid']: raise ValueError(reason or 'calibrate/recenter the arm')
            if tuple(snapshot[n]['epoch'] for n in ('wroom','s3'))!=state['epochs']:
                raise ValueError('board/IMU restarted; recenter before re-enabling')
            frame,_ = fresh_frame(snapshot,now)
            geometry = state['model'].geometry(frame)
            valid = True
        except ValueError as error: reason = str(error)
        packet = input_packet(geometry,snapshot,valid,reason,state['revision'],state['reverse'],now)
        packet['ui_seen'] = state['ui_seen']
        self.submit(packet)

    def request(self,command,request_id=None):
        """Queue for the worker. An enable's outcome is reported in status['enable_request']."""
        if command not in ("enable_arm","enable_claw","slider_release","slider_zero","slider_hundred","slider_enable"):
            raise ValueError("Unknown robot command")
        with self.lock: generation = self.generation
        self.commands.put((command,generation,self.clock(),request_id))

    def request_replay(self,action,revision=None,request_id=None):
        if action not in ('record','play','play_smart','resume','stop'): raise ValueError('Unknown replay action')
        if action=='stop':
            self.pause('Replay stopped by operator')
            return
        with self.lock: generation = self.generation
        self.commands.put((dict(action=action,revision=revision,id=request_id),generation,self.clock(),None))

    def pause(self,reason="operator pause"):
        if self.haptics is not None: self.haptics.stop(reason)
        with self.lock:
            self.generation += 1  # Invalidates queued enables, including one being checked.
            self.pause_pending = reason

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.status)
            result['tuning_requested'] = self.tuning_requested
            result['tuning_pending'] = tuning_values(self.pending_config) if self.pending_config else None
            result['can_reconnect'] = not self.closed.is_set() and not result['connected'] and not self.thread.is_alive()
            result.update(port=self.port,mapping_mode=self.config['mapping_mode'],
                          alignment_deg_s=self.config['alignment_deg_s'],startup_tuning=deepcopy(self.startup_tuning))
            return result

    def tune(self,changes,revision=None):
        """Validate atomically; coalesce slider input and apply at a cycle boundary."""
        with self.lock:
            if self.closed.is_set(): raise ValueError('Controller is closed')
            if revision is not None and (type(revision) is not int or revision!=self.tuning_requested):
                raise ValueError('Settings changed. Review the applied values and try again.')
            self.pending_config = apply_values(self.pending_config or self.config,changes)
            self.tuning_requested += 1
            return self.tuning_requested

    def apply_pending_tuning(self):
        with self.lock:
            previous = self.config
            if self.pending_config is None: return previous
            self.config,self.pending_config = self.pending_config,None
            self.tuning_applied = self.tuning_requested
            self.claw.speed = self.config['claw_speed_per_s']
            self.claw.load_limit = control_values(self.config)['gripper_max_load']
            self.claw.load_stops_enabled = self.config.get('load_stops_enabled',False)
            self.claw.grasp.contact_load = control_values(self.config)['gripper_contact_load']
            self.claw.grasp.hold_load = control_values(self.config)['gripper_hold_load']
        logging.getLogger('wearable').info('Live tuning applied: %s; dashboard controls: %s',tuning_values(self.config),control_values(self.config))
        if self.enabled and not self.aligning:
            self.note(f"LIVE calibrated URDF following ({self.config['speed_deg_s']:g} deg/s)" if self.config['mapping_mode']=='absolute'
                      else f"LIVE relative following ({self.config['speed_deg_s']:g} deg/s)")
        return previous

    def enable_reply(self,request_id,ok,detail):
        """Final outcome of a console enable request; slider/replay commands carry no id."""
        if request_id is None: return
        with self.lock: self.status['enable_request'] = dict(id=request_id,ok=bool(ok),detail=str(detail))

    def note(self,message):
        with self.lock:
            changed = message!=self.status["message"]
            self.status["message"] = message
        if changed: logging.getLogger("wearable").info("Physical robot: %s",message)

    def checked_packet(self,generation=None,epoch=None):
        now = self.clock()
        with self.lock:
            packet,current_generation = self.packet,self.generation
        if self.closed.is_set(): raise ValueError("controller is closing")
        if generation is not None and generation!=current_generation: raise ValueError("operator pause")
        if packet is None or not packet["valid"]:
            raise ValueError(packet.get("reason") or "calibrate/recenter the arm first" if packet else "waiting for wearable readings")
        if 'ui_seen' in packet:
            ui_age = now-packet['ui_seen']
            if not math.isfinite(ui_age) or not 0<=ui_age<1.:
                raise ValueError(f'wearable UI unresponsive ({ui_age*1000:.0f} ms); following paused')
        # Check the producer heartbeat first. Old samples in an old packet do
        # not prove the serial sensors stopped; the UI may have stalled instead.
        for key,label in (('published','wearable UI update'),('seen','IMU data')):
            age = now-packet[key]
            if not math.isfinite(age) or not 0<=age<.3:
                raise ValueError(f'{label} stale ({age*1000:.0f} ms); following paused')
        if epoch is not None and packet["epoch"]!=epoch:
            raise ValueError("sensor/reference/direction changed; re-enable from the current pose")
        # Short presses belong to the gesture detector. The hold gesture still stops everything.
        if self.clicks.held(now): raise ValueError('joystick hold stop')
        if packet['joystick']['pressed'] and self.clicks.suppressed: raise ValueError('Release joystick button before enabling')
        joystick = {**packet['joystick'],'pressed':False}
        self.claw.fresh(joystick,now)
        return {**packet,'joystick':joystick}

    def source_target(self,packet):
        if packet["epoch"]!=self.mapping_epoch:
            self.retarget = Retargeter(URDF)
            self.mapping_epoch = packet["epoch"]
        return self.retarget.targets(packet["geometry"],packet["reverse"])

    def pause_motion(self,arm,reason):
        if self.haptics is not None: self.haptics.stop(reason)
        had_control = self.enabled or self.claw.enabled or self.replay.busy or self.grip_hold_active
        self.grip_hold_active = False
        if self.replay.busy or self.replay.phase=='recording': self.replay.stop(reason)
        if self.smart is not None and not self.smart.finished: self.smart.finish(reason)
        self.clicks.reset()
        self.enabled = False
        self.aligning = False
        self.alignment = None
        self.claw.pause(reason)
        try: self.slider.stop(reason)
        finally:
            if had_control: arm.hold_enabled()
        self.note("PAUSED: "+reason+". Manual enable required.")

    def recorded_pose(self,positions):
        pose = self.grasp_pose(positions)
        if self.replay.has_slider:
            if not self.slider.enabled or self.slider.reference is None:
                raise ValueError('Slider control stopped during recording')
            pose['slider'] = self.slider.position
        return pose

    def grasp_pose(self,positions):
        pose = dict(positions)
        # Retain the closing intention during contact so replay can grasp an object
        # instead of merely touching the exact previously measured jaw position.
        if self.claw.grasp.contacted: pose['gripper'] = self.claw.grasp.request
        return pose

    def replay_raw(self,pose):
        return {**pose,'slider':self.slider.raw_target(pose['slider'])} if 'slider' in pose else dict(pose)

    def smart_arrival(self,pose,positions):
        return self.replay_arrival(self.replay_raw(pose),positions)

    def smart_shift(self,pose,degrees):
        """The pose with some arm joints turned by URDF degrees, the units the agent is shown."""
        direction = self.config['raw_direction']
        angles = self.joint_mapping.urdf_angles(pose,self.limits,direction)
        for name,value in degrees.items(): angles[name] += math.radians(value)
        return {**pose,**self.joint_mapping.raw_targets(angles,self.limits,direction)}

    def smart_turn(self,pose,degrees_left):
        """The pose panned to the arm's left (counter-clockwise seen from above), whatever the URDF's pan sign is."""
        frames = self.retarget.kinematics.frames
        angles = self.joint_mapping.urdf_angles(pose,self.limits,self.config['raw_direction'])
        def heading(value):
            found = frames({**angles,'shoulder_pan':value})
            end,base = found['gripper_frame_joint'][0],found['shoulder_pan'][0]
            return math.atan2(end[1]-base[1],end[0]-base[0])
        change = heading(angles['shoulder_pan']+.01)-heading(angles['shoulder_pan'])
        change = (change+math.pi)%(2*math.pi)-math.pi
        return self.smart_shift(pose,{'shoulder_pan':degrees_left if change>=0 else -degrees_left})

    def smart_nudge(self,pose,direction,centimetres):
        """The pose with the gripper tip moved a short straight step, solved from the URDF's forward kinematics."""
        kinematics = self.retarget.kinematics
        def tip(angles):
            frames = kinematics.frames(angles)
            end,base = frames['gripper_frame_joint'][0],frames['shoulder_pan'][0]
            return tuple(end[i]-base[i] for i in range(3))
        angles = self.joint_mapping.urdf_angles(pose,self.limits,self.config['raw_direction'])
        solved = step_joints(tip,angles,direction_vector(tip(angles),direction,centimetres/100),
                             ('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex'),limits=kinematics.limits)
        return {**pose,**self.joint_mapping.raw_targets(solved,self.limits,self.config['raw_direction'])}

    def smart_arm_state(self,positions,loads,arm):
        """What the agent is told about the arm: URDF degrees, gripper percent open, and the inferred hold."""
        limits = {n:arm.limits(n) for n in BODY}
        angles = self.joint_mapping.urdf_angles(positions,limits,self.config['raw_direction'])
        low,high = arm.limits('gripper')
        return dict(joints={n:round(math.degrees(angles[n]),1) for n in BODY},
                    gripper=round(100*(positions['gripper']-low)/(high-low),1),
                    holding=self.claw.grasp.holding,closed_endpoint=self.claw.grasp.closed_endpoint,
                    load=abs(loads['gripper']))

    def end_replay(self,arm,claw,desired,reason):
        if self.claw.grasp.holding:
            self.slider.stop(reason)
            arm.hold_enabled()
            self.checked_packet(self.replay_generation,self.replay_epoch)
            arm.move_gripper(claw)
            self.grip_hold_active = True
            self.grip_hold_desired = desired['gripper']
            self.replay.stop(reason+'; holding object. Stop releases grip pressure; enable claw for live control.')
            self.note(self.replay.detail)
        else: self.pause_motion(arm,reason+'; holding final pose')

    def replay_arrival(self,target,positions):
        measured = dict(positions)
        if self.replay.has_slider: measured['slider'] = self.slider.last_raw
        tolerance = {n:self.config['alignment_tolerance_deg']*4095/360 for n in BODY}
        tolerance['gripper'] = 15
        tolerance['slider'] = 15
        return PoseArrival(target,measured,self.timer(),tolerance,self.config)

    def replay_action(self,action,arm,positions,loads,generation):
        packet = self.checked_packet(generation)
        if action=='record':
            if self.replay.phase=='recording':
                self.replay.sample(self.recorded_pose(positions),self.timer(),force=True)
                self.replay.stop('Recording ready. Single-click to return to the first pose and replay.')
                return
            if self.replay.busy: raise ValueError('Stop replay before recording')
            if self.aligning: raise ValueError('Wait for arm alignment before recording')
            if not (self.enabled or self.claw.enabled or self.slider.enabled):
                raise ValueError('Enable live arm, claw or slider control before recording')
            pose,limits = self.grasp_pose(positions),{n:arm.limits(n) for n in JOINTS}
            if self.slider.enabled:
                pose['slider'],limits['slider'] = self.slider.position,(0,100)
            self.replay.record(pose,limits,self.timer(),time.time())
            self.smart = None
            return
        if action=='resume':
            if self.replay.phase!='smart' or self.smart is None: raise ValueError('Smart replay is not running')
            self.smart.resume(self.timer())
            return
        if self.replay.phase=='recording': return  # A single click never ends a recording.
        if self.replay.phase!='ready': raise ValueError('Record a motion first')
        self.check_arm_load(loads)
        if self.claw.load_stops_enabled and abs(loads['gripper'])>=self.claw.load_limit:
            raise ValueError('High gripper load; replay refused')
        if any(self.replay.limits[n]!=arm.limits(n) for n in JOINTS):
            raise ValueError('Robot travel calibration changed; record again')
        if any(self.claw.calibration.normalized(packet['joystick']['x'],packet['joystick']['y']).values()):
            raise ValueError('Center joystick X/Y before replay')
        measured = arm.validate_motion()
        gripper = arm.validate_gripper()
        first = self.replay_raw(self.replay.frames[0])
        self.checked_packet(generation,packet['epoch'])
        if action=='play_smart':
            if self.agent_inbox is None: raise ValueError('Smart replay needs the assist (Gemini) link; start the full demo')
            self.replay.play_smart(self.timer())
        else: self.replay.play(self.timer())
        self.replay_epoch,self.replay_generation = packet['epoch'],generation
        self.enabled,self.aligning = False,False
        self.claw.pause('Replay controls the claw')
        self.grip_hold_active = False
        self.claw.low,self.claw.high = arm.limits('gripper')
        self.claw.previous = gripper
        self.previous,self.limits = dict(measured),{n:arm.limits(n) for n in BODY}
        self.replay_gripper = gripper
        arm.latch_and_enable(measured)
        self.checked_packet(generation,packet['epoch'])
        arm.latch_gripper_and_enable(gripper)
        if self.replay.has_slider:
            self.slider.enable(arm,packet['joystick'],self.clock(),
                               before_write=lambda:self.checked_packet(generation,packet['epoch']))
        elif self.slider.enabled: self.slider.stop('Slider is not part of this recording')
        if action=='play_smart':
            self.smart = SmartReplay(self.replay,self.smart_arrival,
                                     SimpleNamespace(nudge=self.smart_nudge,shift=self.smart_shift,turn=self.smart_turn),self.timer(),
                                     self.recorded_pose({**measured,'gripper':gripper}),self.config.get('smart_replay'),
                                     run=f'{time.time():.0f}-{self.replay.revision}')
            self.note('Smart replay: holding still until Gemini chooses an action')
            return
        self.replay_pose_arrival = self.replay_arrival(first,{**measured,'gripper':gripper})
        self.note('Replay: returning to the recorded first pose')

    def replay_cycle(self,arm,positions,loads,torque,dt,previous_config):
        packet = self.checked_packet(self.replay_generation,self.replay_epoch)
        if not all(torque[n] for n in JOINTS): raise ValueError('Replay servo torque switched off')
        self.check_arm_load(loads)
        if self.claw.load_stops_enabled and abs(loads['gripper'])>=self.claw.load_limit:
            raise ValueError('High gripper load during replay')
        measured = dict(positions)
        if self.replay.has_slider: measured['slider'] = self.slider.last_raw
        smart = self.replay.phase=='smart'
        if smart:
            if self.agent_inbox is not None: self.smart.submit(self.agent_inbox.latest(),self.timer())
            desired = self.replay_raw(self.smart.step(self.timer(),self.recorded_pose(positions),positions,measured,self.claw.grasp))
        else: desired = self.replay_raw(self.replay.target(self.timer()))
        target = bounded_targets({n:desired[n] for n in BODY},self.previous,positions,self.limits,dt,self.config,previous_config=previous_config)
        claw = self.claw.position_target(desired['gripper'],positions['gripper'],loads['gripper'],dt,self.timer())
        self.checked_packet(self.replay_generation,self.replay_epoch)
        arm.move_raw(target)
        self.checked_packet(self.replay_generation,self.replay_epoch)
        arm.move_gripper(claw)
        self.previous,self.replay_gripper = target,claw
        if self.replay.has_slider:
            self.slider.step(packet['joystick'],dt,self.clock(),target_raw=desired['slider'],
                             before_write=lambda:self.checked_packet(self.replay_generation,self.replay_epoch))
        if smart:
            self.replay.detail = self.smart.detail
            if self.smart.finished: self.end_replay(arm,claw,desired,'Smart replay ended: '+self.smart.detail)
        elif self.replay.phase=='returning' and self.replay_pose_arrival.update(measured,self.timer(),self.claw.grasp.contacted):
            self.replay.start_timeline(self.timer())
            self.note('Replaying recorded robot motion')
        elif self.replay.phase=='playing' and self.replay.elapsed>=self.replay.duration:
            self.replay.phase = 'finishing'
            self.replay.revision += 1
            self.replay.detail = 'Finishing at the recorded last pose.'
            self.replay_pose_arrival = self.replay_arrival(desired,positions)
        elif self.replay.phase=='finishing' and self.replay_pose_arrival.update(measured,self.timer(),self.claw.grasp.contacted):
            self.end_replay(arm,claw,desired,'Replay complete')
        return {**target,'gripper':claw},{n:desired[n] for n in BODY}

    def cycle(self,arm,dt):
        previous_config = self.apply_pending_tuning()
        self.refresh_input()
        # No cached telemetry may authorize a motion write.
        telemetry_started = self.timer()
        sampled_at = time.time()
        positions = arm.bus.sync_read("Present_Position",list(JOINTS),normalize=False,num_retry=0)
        loads = arm.bus.sync_read("Present_Load",list(JOINTS),normalize=False,num_retry=0)
        torque = arm.bus.sync_read("Torque_Enable",list(JOINTS),normalize=False,num_retry=0)
        read_ms = 1000*(self.timer()-telemetry_started)
        if dt>=.001: self.intervals.append(dt)  # Exclude the first, near-zero startup interval.
        loop_hz = len(self.intervals)/sum(self.intervals) if self.intervals else 0.
        if any(set(values)!=set(JOINTS) or not all(math.isfinite(v) for v in values.values())
               for values in (positions,loads,torque)):
            raise ValueError("invalid robot telemetry")
        with self.lock:
            generation = self.generation
            pause = self.pause_pending
            self.pause_pending = None
        if pause: self.pause_motion(arm,pause)
        try:
            input_packet = self.checked_packet()
            can_enable = True
            input_status = "Wearable input LIVE"
        except (ValueError,KeyError,TypeError) as error:
            can_enable = False
            input_status = str(error)
            self.clicks.reset()
        if can_enable:
            with self.lock: raw_joystick = dict(self.packet['joystick'])
            gesture = self.clicks.update(raw_joystick['pressed'],input_packet['epoch'],self.clock())
            if gesture=='stop' or (self.replay.busy and self.clicks.just_pressed):
                self.pause('Joystick stopped motion')
                self.pause_motion(arm,'Joystick stopped motion')
            elif gesture is not None:
                self.request_replay(gesture)
        desired = {}
        if can_enable and self.config['mapping_mode']=='absolute':
            desired = self.joint_mapping.raw_targets(self.source_target(input_packet),
                       {n:arm.limits(n) for n in BODY},self.config['raw_direction'])
        new_arm = new_claw = new_replay = False
        while True:
            try: command,requested_generation,requested_at,request_id = self.commands.get_nowait()
            except queue.Empty: break
            if requested_generation!=generation or self.clock()-requested_at>.5:
                self.enable_reply(request_id,False,'Cancelled by Stop/reset before the robot could act; try again.')
                continue
            if isinstance(command,dict):
                error = None
                was_busy = self.replay.busy
                try:
                    revision = command['revision']
                    if revision is not None and (type(revision) is not int or revision!=self.replay.revision):
                        raise ValueError('Replay state changed; try again')
                    self.replay_action(command['action'],arm,positions,loads,generation)
                    new_replay = self.replay.busy and not was_busy
                except (ValueError,KeyError,TypeError,RuntimeError,ConnectionError,OSError) as exc:
                    error = str(exc)
                    if self.replay.busy and not was_busy: self.pause_motion(arm,error)
                    self.replay.detail = error
                if command['id']:
                    self.replay.request = dict(id=command['id'],ok=error is None,applied=error is None,
                                               detail=error or self.replay.detail)
                continue
            try:
                if self.replay.busy:
                    raise ValueError('Stop replay before changing live control')
                if self.replay.phase=='recording' and command in ('slider_release','slider_zero','slider_hundred'):
                    self.replay.stop('Recording finished because the slider reference changed')
                if command in ('slider_release','slider_zero','slider_hundred'):
                    if command=='slider_release': self.slider.release(arm)
                    else: self.slider.set_reference(arm,0 if command=='slider_zero' else 100)
                    continue
                packet = self.checked_packet(generation)
                if command=='slider_enable':
                    if not self.slider.enabled:
                        self.slider.enable(arm,packet['joystick'],self.clock(),
                                           before_write=lambda:self.checked_packet(generation,packet['epoch']))
                        self.slider_epoch,self.slider_generation = packet['epoch'],generation
                    continue
                if command=="enable_arm":
                    if self.enabled:
                        self.enable_reply(request_id,True,'Arm already enabled.')
                        continue
                    self.check_arm_load(loads)
                    arm.validate_motion()
                    measured = arm.positions()
                    packet = self.checked_packet(generation,packet["epoch"])
                    source = self.source_target(packet)
                    self.anchor,self.source_anchor,self.previous = dict(measured),dict(source),dict(measured)
                    self.limits = {n:arm.limits(n) for n in BODY}
                    self.aligning = self.config['mapping_mode']=='absolute'
                    self.alignment_target = self.joint_mapping.raw_targets(source,self.limits,self.config['raw_direction'])
                    self.alignment = Alignment(self.alignment_target,measured,self.limits,self.timer(),self.config) if self.aligning else None
                    # Validate the final measured pose before the first write.
                    bounded_follow(measured,source,source,measured,measured,self.limits,0,self.config)
                    self.checked_packet(generation,packet["epoch"])
                    arm.latch_and_enable(measured)
                    self.enabled = True
                    self.active_epoch = packet["epoch"]
                    self.active_generation = generation
                    new_arm = True
                    if self.aligning:
                        self.note(f"ALIGNING to captured URDF pose ({self.config['alignment_deg_s']:g} deg/s). Hold your arm still; Stop holds.")
                    else: self.note(f"LIVE relative following ({self.config['speed_deg_s']:g} deg/s)")
                    # Claw and haptics come on with arm tracking by default; a claw refusal never blocks the arm.
                    if self.auto_enable_claw and not self.claw.enabled and not self.grip_hold_active:  # Never silently release a held grasp.
                        try:
                            self.enable_claw_control(arm,loads,generation,packet)
                            new_claw = True
                        except (ValueError,KeyError,TypeError,RuntimeError,ConnectionError,OSError) as error:
                            self.claw.pause(f"not auto-enabled: {error}")
                            self.note(f"{self.status['message']} · Claw not auto-enabled: {error}; click Enable claw")
                    if self.auto_enable_claw and self.haptics is not None: self.haptics.enable()
                    self.enable_reply(request_id,True,self.status['message'])
                else:
                    if self.claw.enabled:
                        self.enable_reply(request_id,True,'Claw already enabled.')
                        continue
                    self.enable_claw_control(arm,loads,generation,packet)
                    new_claw = True
                    self.note("Claw enabled: Y=0 open, Y=4095 closed; neutral halfway")
                    self.enable_reply(request_id,True,self.status['message'])
            except (ValueError,KeyError,TypeError,RuntimeError,ConnectionError,OSError) as error:
                self.pause_motion(arm,f"Enable refused: {error}")
                self.enable_reply(request_id,False,f"Enable refused: {error}")
        targets = {}
        limited = []
        flags = {}
        try:
            replaying = self.replay.busy
            if replaying and not new_replay:
                if self.timer()-telemetry_started>=.25: raise ValueError('Robot telemetry too slow for replay')
                targets,desired = self.replay_cycle(arm,positions,loads,torque,dt,previous_config)
            if self.slider.enabled and not replaying:
                packet = self.checked_packet(self.slider_generation,self.slider_epoch)
                self.slider.step(packet['joystick'],dt,self.clock(),
                                 before_write=lambda:self.checked_packet(self.slider_generation,self.slider_epoch))
            if self.enabled and not new_arm and not replaying:
                if self.timer()-telemetry_started>=.25: raise ValueError("robot telemetry too slow/stale")
                packet = self.checked_packet(self.active_generation,self.active_epoch)
                self.check_arm_load(loads)
                if not all(torque[n] for n in BODY):
                    raise ValueError("arm torque switched off")
                if self.config['mapping_mode']=='relative':
                    source = self.source_target(packet)
                    desired = {n:self.anchor[n]+self.config['raw_direction'][n]*(source[n]-self.source_anchor[n])*TICKS_PER_RADIAN for n in BODY}
                elif self.aligning:
                    desired = dict(self.alignment_target)
                    if self.alignment.update(positions,self.timer()):
                        self.aligning = False
                        self.alignment = None
                        self.note(f"LIVE calibrated URDF following ({self.config['speed_deg_s']:g} deg/s)")
                else:
                    desired = self.joint_mapping.raw_targets(self.source_target(packet),self.limits,self.config['raw_direction'])
                motion_config = {**self.config,'speed_deg_s':self.config['alignment_deg_s']} if self.aligning else self.config
                # When reducing lead, accept the preceding command under its
                # old bound once, then brake into the new tighter measured bound.
                target = bounded_targets(desired,self.previous,positions,self.limits,dt,motion_config,limited,flags,previous_config)
                self.checked_packet(self.active_generation,self.active_epoch)
                arm.move_raw(target)
                self.previous = target
                targets.update(target)
            if self.claw.enabled and not new_claw and not replaying:
                if self.timer()-telemetry_started>=.25: raise ValueError("robot telemetry too slow/stale")
                packet = self.checked_packet(self.claw_generation,self.claw_epoch)
                if not torque["gripper"]: raise ValueError("claw torque switched off")
                target = self.claw.step(packet["joystick"],positions["gripper"],loads["gripper"],dt,self.clock())
                self.checked_packet(self.claw_generation,self.claw_epoch)
                arm.move_gripper(target)
                targets["gripper"] = target
            elif self.grip_hold_active and not replaying:
                if self.timer()-telemetry_started>=.25: raise ValueError('Robot telemetry too slow for grasp hold')
                self.checked_packet(self.replay_generation,self.replay_epoch)
                if not torque['gripper']: raise ValueError('Claw torque switched off during grasp hold')
                target = self.claw.position_target(self.grip_hold_desired,positions['gripper'],loads['gripper'],dt,self.timer())
                self.checked_packet(self.replay_generation,self.replay_epoch)
                arm.move_gripper(target)
                targets['gripper'] = target
                if not self.claw.grasp.holding:
                    self.grip_hold_active = False
                    self.note('Grasp contact lost; claw held. Enable claw to resume.')
            if self.replay.phase=='recording':
                self.checked_packet(generation)
                self.replay.note_holding(self.claw.grasp.holding,self.timer())
                self.replay.sample(self.recorded_pose(positions),self.timer())
        except (ValueError,KeyError,TypeError,RuntimeError,ConnectionError,OSError) as error:
            self.pause_motion(arm,str(error))
            targets = {}
        if self.smart is not None:
            self.replay.agent = {**self.smart.snapshot(),'arm':self.smart_arm_state(positions,loads,arm)}
        with self.lock:
            self.status.update(connected=True,enabled=self.enabled or self.replay.busy,claw_enabled=self.claw.enabled or self.replay.busy or self.grip_hold_active,can_enable=can_enable,
                               seen=self.clock(),positions=positions,loads=loads,torque=torque,targets=targets,
                               loop_hz=loop_hz,read_ms=read_ms,limited=limited,input_status=input_status)
            self.status.update(desired=desired,flags=flags,aligning=self.aligning,
                               alignment_progress=self.alignment.detail if self.alignment else '')
            self.status.update(tuning=tuning_values(self.config),controls=control_values(self.config),tuning_applied=self.tuning_applied,
                               slider=self.slider.snapshot(),replay=self.replay.snapshot(),
                               grasp={**self.claw.grasp.snapshot(),'load':abs(loads['gripper'])},grip_hold_active=self.grip_hold_active)
            limits = {n:arm.limits(n) for n in BODY}
            measured_angles = self.joint_mapping.urdf_angles(positions,limits,self.config['raw_direction'])
            requested_angles = self.joint_mapping.urdf_angles(desired,limits,self.config['raw_direction']) if desired else {}
            self.status['matching'] = {n:dict(actual_deg=math.degrees(measured_angles[n]),
                                             preview_deg=math.degrees(requested_angles[n]) if requested_angles else None)
                                       for n in BODY}
        if self.telemetry is not None and self.timer() >= self.telemetry.next_sample:
            self.telemetry.publish(positions=positions,loads=loads,targets=targets,
                limits={n:arm.limits(n) for n in JOINTS},mapping=self.joint_mapping,direction=self.config['raw_direction'],
                enabled=self.enabled or self.claw.enabled or self.replay.busy or self.grip_hold_active,input_fresh=can_enable,
                slider=self.slider.snapshot(),
                sampled_at=sampled_at,now=self.timer(),sensor_now=self.clock(),
                wearable_snapshot=self.source_hub.snapshot() if self.source_hub is not None else None)
        if self.clock()>=self.next_timing_log:
            logging.getLogger("wearable").info("Physical loop %.1f Hz; reads %.1f ms; arm=%s claw=%s; range-limited=%s",
                                              loop_hz,read_ms,self.enabled,self.claw.enabled,limited)
            if self.enabled:
                logging.getLogger('wearable').info('Physical joints desired=%s sent=%s actual=%s load=%s limiting=%s',
                    {n:round(v) for n,v in desired.items()},{n:round(v) for n,v in targets.items()},positions,loads,flags)
            if self.claw.enabled or self.grip_hold_active or self.replay.busy:
                logging.getLogger('wearable').info('Physical claw sent=%s actual=%s load=%s grasp=%s',
                    targets.get('gripper'),positions['gripper'],loads['gripper'],self.claw.grasp.snapshot()['detail'])
            self.next_timing_log = self.clock()+10

    def check_arm_load(self,loads):
        if self.config.get('load_stops_enabled',False):
            joint = max(BODY,key=lambda name:abs(loads[name]))
            if abs(loads[joint])>=self.config['max_load']:
                raise ValueError(f"high arm load: {joint} {abs(loads[joint]):g} >= {self.config['max_load']:g} raw counts")

    def enable_claw_control(self,arm,loads,generation,packet):
        """Latch the gripper and start joystick position control; raises on any refusal."""
        if self.claw.load_stops_enabled and abs(loads["gripper"])>=self.claw.load_limit:
            raise ValueError(f"high claw load: {abs(loads['gripper']):g} >= {self.claw.load_limit:g} raw counts")
        measured = arm.validate_gripper()
        packet = self.checked_packet(generation,packet["epoch"])
        self.claw.reverse = packet["reverse"]["gripper"]
        self.claw.enable(packet["joystick"],measured,arm.limits("gripper"),self.clock())
        self.grip_hold_active = False
        self.checked_packet(generation,packet["epoch"])
        arm.latch_gripper_and_enable(measured)
        self.claw_epoch,self.claw_generation = packet["epoch"],generation

    def run(self):
        arm = None
        try:
            if self.telemetry_endpoint:
                from robot_control.telemetry import WearableTelemetry
                self.telemetry = WearableTelemetry(self.telemetry_endpoint)
                logging.getLogger('wearable').info('IMU robot telemetry: %s',self.telemetry_endpoint)
            arm = self.arm_factory(self.port)
            if not self.connect_arm(arm): return
            arm.bus.set_timeout(150)
            self.note(f"{self.port}: connected, read only. Calibrate, then enable arm/claw.")
            logging.getLogger('wearable').info('Preview-to-motor degree gains=%s; physical trim degrees=%s; claw travel/s=%.1f',
                self.joint_mapping.degree_gains({n:arm.limits(n) for n in BODY},self.config['raw_direction']),
                self.joint_mapping.trim_deg,self.claw.speed)
            previous = self.timer()
            while not self.closed.is_set():
                start = self.timer()
                self.cycle(arm,start-previous)
                previous = start
                # Python 3.12 uses a high-resolution sleep on Windows. Event.wait
                # rounds short waits to coarse scheduler ticks on this machine.
                # At most one configured period (10 ms by default) to shutdown.
                remaining = 1/self.config["control_hz"]-(self.timer()-start)
                if remaining>0 and not self.closed.is_set(): time.sleep(remaining)
        except Exception as error:
            logging.getLogger("wearable").exception("Physical robot connection/control stopped")
            if 'Missing motor IDs:' in str(error):
                self.note(f"OFF: {self.port} opened but expected motors did not reply. Check motor power and bus cable, then Reconnect robot. Details are in runtime.log.")
            else:
                self.note(f"OFF: {type(error).__name__}: {error}. Use Reconnect robot after checking the connection.")
        finally:
            if self.haptics is not None: self.haptics.stop('robot controller stopped')
            self.enabled = False
            self.aligning = False
            self.claw.pause("controller stopped")
            self.grip_hold_active = False
            self.replay.stop('Controller stopped; recording kept for explicit replay')
            self.clicks.reset()
            try: self.slider.stop('Controller stopped')
            except Exception: logging.getLogger('wearable').exception('Could not stop slider')
            if arm is not None:
                try: arm.hold_enabled()
                except Exception: logging.getLogger("wearable").exception("Could not send robot hold")
                try: arm.close()
                except Exception: logging.getLogger("wearable").exception("Could not close robot port")
            if self.telemetry is not None:
                self.telemetry.close()
                self.telemetry = None
            with self.lock: self.status.update(connected=False,enabled=False,claw_enabled=False,can_enable=False,
                                               aligning=False,alignment_progress='',slider=self.slider.snapshot(),replay=self.replay.snapshot(),grasp=self.claw.grasp.snapshot(),grip_hold_active=False)

    def close(self):
        if self.haptics is not None: self.haptics.close()
        if self.agent_inbox is not None and hasattr(self.agent_inbox,'close'): self.agent_inbox.close()
        self.pause("window closed")
        self.closed.set()
        if self.thread.is_alive(): self.thread.join(timeout=3)
