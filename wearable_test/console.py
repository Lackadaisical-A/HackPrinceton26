"""Web-console commands and status, executed on Wearable Lab's UI thread.

The browser reaches this module through the bridge (WebSocket -> ZMQ 5557) and
reads status on 5562. Commands reuse the desktop buttons' capture, validation,
persistence, pause and enable paths; this transport never owns a serial port.
Arm/claw enable only queues WearableRobot.request(): the serial worker re-checks
fresh input, load and limits exactly as for the desktop button and reports the
outcome. Stop needs no session, revision or clock agreement, so it is always
honoured. Closing the browser does not stop the arm; the Tk tick stays the
safety heartbeat.
"""
import json
import logging
import math
import time
from collections import deque
from uuid import uuid4

import zmq

from robot_control.haptics import MOTORS
from robot_control.joints import BODY, JOINTS
from robot_control.tuning import RESPONSE_PROFILES, response_profile, response_profile_index
from wearable_test.io import driver_status, fresh_frame
from wearable_test.model import AXES, ROLES

LOG = logging.getLogger('wearable')
TUNE_KEYS = frozenset({'speed_deg_s', 'claw_speed_per_s', 'max_load', 'gripper_max_load', 'load_stops_enabled',
                       'gripper_contact_load', 'gripper_hold_load', 'response_ms', 'control_hz', 'joint_lead_deg'})
ARM_LENGTHS = ('upper_cm', 'forearm_cm', 'hand_cm')
ARM_POSITIONS = ('bicep_from_elbow_pct', 'forearm_from_wrist_pct', 'hand_from_wrist_pct')
AXIS_OPTIONS = ('Auto', *AXES)
REPLAY_BUSY = ('returning', 'playing', 'finishing', 'smart')
JOINT_NAMES = dict(shoulder_pan='Base turn', shoulder_lift='Shoulder lift', elbow_flex='Elbow bend',
                   wrist_flex='Wrist bend', wrist_roll='Wrist roll', gripper='Claw')


def is_number(value):
    """A finite int/float; bools and NaN/inf are refused."""
    return type(value) in (int, float) and math.isfinite(value)


def num(value, digits=1):
    """JSON-safe rounded number: the status is serialized with allow_nan=False."""
    return round(float(value), digits) if is_number(value) else None


def robot_fresh(state, now):
    return (isinstance(state, dict) and bool(state.get('connected')) and is_number(state.get('seen'))
            and 0 <= now - state['seen'] < .5)


def arm_settings_error(settings):
    """Strict check for browser input; ArmModel then applies its own validation."""
    if not isinstance(settings, dict) or set(settings) != {*ARM_LENGTHS, *ARM_POSITIONS, 'axes'}:
        return 'Send all six measurements and the three sensor axes.'
    if any(not is_number(settings[key]) or not 5 <= settings[key] <= 60 for key in ARM_LENGTHS):
        return 'Segment lengths must be 5-60 cm.'
    if any(not is_number(settings[key]) or not 0 <= settings[key] <= 100 for key in ARM_POSITIONS):
        return 'Sensor positions must be 0-100% along their segment.'
    axes = settings['axes']
    if not isinstance(axes, dict) or set(axes) != set(ROLES) or any(axes[role] not in AXIS_OPTIONS for role in ROLES):
        return 'Choose Auto or one board axis for each sensor.'
    return None


def response_values(values):
    if not isinstance(values, dict): return None
    leads = values.get('joint_lead_deg') or {}
    return dict(speed_deg_s=num(values.get('speed_deg_s'), 2), response_ms=num(values.get('response_ms'), 2),
                control_hz=num(values.get('control_hz'), 2), joint_lead_deg={name: num(leads.get(name), 2) for name in BODY})


def profile_index(values):
    try: return response_profile_index(values) if isinstance(values, dict) else None
    except (TypeError, ValueError, KeyError): return None


class CalibrationConsole:
    def __init__(self, app, commands_endpoint='tcp://127.0.0.1:5557',
                 publish_endpoint='tcp://127.0.0.1:5562'):
        self.app = app
        self.session = uuid4().hex
        self.revision = 0
        self.request = None
        self.tuning_request = None
        self.slider_request = None
        self.reconnect_request = None
        self.replay_request = None
        self.control_request = None
        self.arm_settings_request = None
        self.directions_request = None
        self.coins_request = None
        self.block_errors = set()
        self.publish_error = None
        self.handlers = {'calibrate_arm': self.calibrate, 'tune_robot': self.tune, 'control_slider': self.control_slider,
                         'reconnect_robot': self.reconnect, 'replay_robot': self.replay_command,
                         'control_robot': self.control_robot, 'configure_arm': self.configure_arm,
                         'set_joint_direction': self.set_joint_direction, 'test_haptic_motor': self.test_haptic_motor}
        self.seen = deque(maxlen=128)
        self.next_publish = 0.
        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.publisher = self.context.socket(zmq.PUB)
        try:
            for socket in (self.subscriber, self.publisher):
                socket.setsockopt(zmq.LINGER, 0)
            self.subscriber.setsockopt(zmq.RCVHWM, 16)
            self.subscriber.setsockopt(zmq.MAXMSGSIZE, 4096)
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, 'command ')
            self.subscriber.connect(commands_endpoint)
            self.publisher.setsockopt(zmq.SNDHWM, 4)
            self.publisher.bind(publish_endpoint)
        except Exception:
            self.close()
            raise

    def invalidate(self):
        """A queued command must not undo a local Stop, reset or calibration edit."""
        self.revision += 1

    def check(self, message, wall, *, session=True, stamp=True, revision=True):
        """Shared transport checks: (request_id, error). A None id means ignore (malformed or repeated)."""
        request_id = message.get('request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or request_id in self.seen:
            return None, None
        self.seen.append(request_id)
        t = message.get('t')
        if session and message.get('session') != self.session:
            return request_id, 'Controller restarted. Wait for fresh status and try again.'
        if stamp and (not is_number(t) or not 0 <= wall - t < 2):
            return request_id, 'Request expired. Check the console clock and try again.'
        if revision and (type(message.get('revision')) is not int or message['revision'] != self.revision):
            return request_id, 'Control state changed. Review the current status and try again.'
        return request_id, None

    def robot_state(self):
        """One worker snapshot, or None with no robot (or a non-dict test double)."""
        worker = self.app.physical
        if worker is None: return None
        try: state = worker.snapshot()
        except Exception: return None
        return state if isinstance(state, dict) else None

    def command(self, message, wall=None):
        wall = time.time() if wall is None else wall
        if not isinstance(message, dict): return
        handler = self.handlers.get(message.get('type'))
        if handler is not None: handler(message, wall)

    def calibrate(self, message, wall):
        request_id = message.get('request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or request_id in self.seen:
            return
        self.seen.append(request_id)
        action = message.get('action')
        stamp = message.get('t')
        error = None
        if message.get('session') != self.session:
            error = 'Controller restarted. Wait for fresh status and try again.'
        elif (not isinstance(stamp, (int, float)) or isinstance(stamp, bool)
              or not math.isfinite(stamp) or not 0 <= wall - stamp < 2):
            error = 'Request expired. Check the console clock and try again.'
        elif action not in ('capture_a', 'capture_b', 'capture_elbow', 'capture_shoulder', 'capture_pan_left', 'capture_pan_right', 'recenter', 'cancel'):
            error = 'Unknown calibration action.'
        elif action != 'cancel' and (type(message.get('revision')) is not int or message['revision'] != self.revision):
            error = 'Calibration changed. Review the current step and try again.'
        elif action == 'cancel':
            self.app.stop_all()
        elif self.app.capture:
            error = 'A capture is already running.'
        elif getattr(self.app, 'settings_dialog', None) is not None:
            error = 'Close the desktop Dimensions dialog first.'
        else:
            self.app.begin_capture({'capture_a': 'A', 'capture_b': 'B', 'capture_elbow':'C', 'capture_shoulder':'D',
                                    'capture_pan_left':'E','capture_pan_right':'F','recenter': 'R'}[action], lift=False)
            if self.app.capture is None:
                error = self.app.message.get()
        self.request = dict(id=request_id, ok=error is None,
                            detail=error or ('Capture cancelled; arm held.' if action == 'cancel' else 'Capture started.'))
        self.next_publish = 0.

    def tune(self, message, wall):
        request_id = message.get('request_id')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80 or request_id in self.seen:
            return
        self.seen.append(request_id)
        error, revision = None, None
        stamp = message.get('t')
        worker = self.app.physical
        state = worker.snapshot() if worker else None
        changes = message.get('changes')
        # Exactly one of: explicit changes, a named response profile, or the startup values.
        modes = [key for key in ('changes', 'profile', 'startup') if message.get(key) is not None]
        if message.get('session') != self.session:
            error = 'Controller restarted. Wait for fresh status and try again.'
        elif (type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=wall-stamp<2):
            error = 'Request expired. Check the console clock and try again.'
        elif (not isinstance(state,dict) or not state.get('connected') or state.get('seen') is None
              or not 0<=time.monotonic()-state['seen']<.5):
            error = 'Robot controller is not connected or its readings are stale.'
        elif type(message.get('revision')) is not int:
            error = 'Missing settings revision.'
        elif len(modes) != 1:
            error = 'Choose speed or load settings to apply.'
        elif modes == ['profile']:
            profile = message['profile']
            if type(profile) is not int or not 0 <= profile < len(RESPONSE_PROFILES):
                error = 'Choose a response profile from Precise to Fast.'
            else: changes = response_profile(profile)
        elif modes == ['startup']:
            changes = state.get('startup_tuning')
            if message['startup'] is not True or not isinstance(changes, dict):
                error = 'Startup response settings are unavailable.'
        elif not isinstance(changes,dict) or not changes or set(changes)-TUNE_KEYS:
            error = 'Choose speed or load settings to apply.'
        if error is None:
            try:
                revision = worker.tune(changes,revision=message['revision'])
            except ValueError as exc:
                error = str(exc)
        self.tuning_request = dict(id=request_id,ok=error is None,revision=revision,
                                   detail=error or 'Applying settings...')
        self.next_publish = 0.

    def tuning_status(self, now, state):
        if not isinstance(state,dict) or 'controls' not in state:
            return None
        request = dict(self.tuning_request) if self.tuning_request else None
        if request:
            request['applied'] = request['ok'] and state['tuning_applied']>=request['revision']
            if request['applied']: request['detail'] = 'Settings applied.'
        return dict(values=state['controls'],revision=state['tuning_requested'],
                    applied_revision=state['tuning_applied'],connected=state['connected'],
                    fresh=state['seen'] is not None and 0<=now-state['seen']<.5,
                    enabled=state['enabled'] or state['claw_enabled'],request=request,
                    can_reconnect=state.get('can_reconnect',False),detail=state.get('message',''),
                    reconnect_request=self.reconnect_request,grasp=state.get('grasp'))

    def reconnect(self,message,wall):
        request_id = message.get('request_id')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80 or request_id in self.seen: return
        self.seen.append(request_id)
        stamp,error = message.get('t'),None
        if message.get('session')!=self.session: error = 'Controller restarted; refresh before reconnecting.'
        elif type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=wall-stamp<2: error = 'Reconnect request expired.'
        elif type(message.get('revision')) is not int or message['revision']!=self.revision: error = 'Control state changed; try again.'
        elif self.app.physical is None: error = 'Start the IMU controller with a robot port first.'
        else:
            try:
                self.app.physical.reconnect()
                self.invalidate()
            except ValueError as exc: error = str(exc)
        self.reconnect_request = dict(id=request_id,ok=error is None,detail=error or 'Reconnecting robot; motion remains off.')
        self.next_publish = 0.

    def control_slider(self,message,wall):
        request_id = message.get('request_id')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80 or request_id in self.seen: return
        self.seen.append(request_id)
        error = None
        stamp = message.get('t')
        action = message.get('action')
        state = self.app.physical.snapshot() if self.app.physical else None
        if message.get('session')!=self.session: error = 'Controller restarted; refresh before controlling slider.'
        elif type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=wall-stamp<2: error = 'Slider request expired.'
        elif action not in ('release','zero','hundred','enable'): error = 'Unknown slider action.'
        elif type(message.get('revision')) is not int or message['revision']!=self.revision: error = 'Control state changed; try again.'
        elif not isinstance(state,dict) or not state.get('connected') or state.get('seen') is None or not 0<=time.monotonic()-state['seen']<.5:
            error = 'Robot controller is unavailable.'
        else:
            self.app.physical.request('slider_'+action)
            self.invalidate()  # Invalidate other queued reference/enable actions.
        self.slider_request = dict(id=request_id,ok=error is None,detail=error or 'Slider action requested; check slider status.')
        self.next_publish = 0.

    def slider_status(self, state):
        if not isinstance(state,dict) or 'slider' not in state: return None
        return {**state['slider'],'request':self.slider_request}

    def replay_command(self,message,wall):
        request_id = message.get('request_id')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80 or request_id in self.seen: return
        self.seen.append(request_id)
        stamp,error = message.get('t'),None
        action = message.get('action')
        state = self.app.physical.snapshot() if self.app.physical else None
        if message.get('session')!=self.session: error = 'Controller restarted; refresh replay status.'
        elif type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=wall-stamp<2: error = 'Replay request expired.'
        elif action not in ('record','play','play_smart','resume','stop'): error = 'Unknown replay action.'
        elif action=='stop': self.app.stop_all()
        elif type(message.get('revision')) is not int or message['revision']!=self.revision: error = 'Control state changed; try again.'
        elif not isinstance(state,dict) or not state.get('connected') or state.get('seen') is None or not 0<=time.monotonic()-state['seen']<.5:
            error = 'Robot readings are unavailable.'
        elif type(message.get('replay_revision')) is not int: error = 'Missing replay revision.'
        else:
            self.app.physical.request_replay(action,revision=message['replay_revision'],request_id=request_id)
        self.replay_request = dict(id=request_id,ok=error is None,applied=error is None and action=='stop',
                                   detail=error or ('Stop requested.' if action=='stop' else 'Waiting for robot confirmation.'))
        self.next_publish = 0.

    def replay_status(self, state):
        if not isinstance(state,dict) or 'replay' not in state: return None
        result = dict(state['replay'])
        if self.replay_request and (not result['request'] or result['request']['id']!=self.replay_request['id']):
            result['request'] = self.replay_request
        return result

    # --- Robot enable / stop ------------------------------------------------------------------

    def enable_blocker(self, action, state, now):
        """Why the browser may not enable now (None if it may). The worker re-checks everything."""
        app = self.app
        if app.physical is None or state is None: return 'Start the IMU controller with a robot port first.'
        if not robot_fresh(state, now): return 'Robot controller is not connected or its readings are stale.'
        if app.capture: return 'Finish the current capture first.'
        if getattr(app, 'settings_dialog', None) is not None: return 'Close the desktop Dimensions dialog first.'
        if not app.model.calibrated: return 'Calibrate or recenter the arm first.'
        if (state.get('replay') or {}).get('phase') in REPLAY_BUSY: return 'Stop replay first.'
        if not state.get('can_enable'): return str(state.get('input_status') or 'Wearable input is not ready.')
        if action == 'enable_arm' and state.get('enabled'): return 'Arm is already enabled.'
        if action == 'enable_claw' and state.get('claw_enabled') and not state.get('grip_hold_active'):
            return 'Claw is already enabled.'
        return None

    def control_robot(self, message, wall):
        action = message.get('action')
        stop = action == 'stop'
        # Stop is idempotent and always safe: no session, revision or clock agreement needed.
        request_id, error = self.check(message, wall, session=not stop, stamp=not stop, revision=not stop)
        if request_id is None: return
        if error is None:
            if stop:
                self.app.stop_all()
            elif action not in ('enable_arm', 'enable_claw'):
                error = 'Unknown robot action.'
            else:
                error = self.enable_blocker(action, self.robot_state(), time.monotonic())
                if error is None: self.app.physical.request(action, request_id=request_id)
        self.control_request = dict(id=request_id, ok=error is None, applied=stop and error is None,
                                    detail=error or ('Stopped: arm and claw held, coins off.' if stop
                                                     else 'Enable requested; waiting for the robot.'))
        self.next_publish = 0.

    def control_status(self, state):
        """The console's acknowledgment, completed by the worker's own enable outcome."""
        request = dict(self.control_request) if self.control_request else None
        reply = state.get('enable_request') if isinstance(state, dict) else None
        if request and isinstance(reply, dict) and reply.get('id') == request['id']:
            request.update(ok=bool(reply.get('ok')), applied=bool(reply.get('ok')), detail=str(reply.get('detail', '')))
        return request

    def physical_status(self, state, now):
        if state is None: return None
        app = self.app
        fresh = robot_fresh(state, now)
        message = str(state.get('message', ''))
        replay_busy = (state.get('replay') or {}).get('phase') in REPLAY_BUSY
        mode = ('replay' if replay_busy else 'aligning' if state.get('aligning') else 'live' if state.get('enabled')
                else 'paused' if message.startswith('PAUSED') else 'off')
        positions, desired = state.get('positions') or {}, state.get('desired') or {}
        targets, matching = state.get('targets') or {}, state.get('matching') or {}
        flags, loads, torque = state.get('flags') or {}, state.get('loads') or {}, state.get('torque') or {}
        joints = []
        for name in JOINTS:
            match = matching.get(name) or {}
            actual, want = positions.get(name), desired.get(name)
            gap = (want-actual)*360/4095 if is_number(want) and is_number(actual) else None
            joints.append(dict(actual_deg=num(match.get('actual_deg')), preview_deg=num(match.get('preview_deg')),
                               position=num(actual, 0), sent=num(targets.get(name), 0), gap_deg=num(gap),
                               flag=str(flags.get(name) or ''), load=num(loads.get(name), 0), torque=bool(torque.get(name))))
        return dict(port=str(state.get('port') or ''), connected=bool(state.get('connected')), fresh=fresh, mode=mode,
                    claw_enabled=bool(state.get('claw_enabled')), grip_hold=bool(state.get('grip_hold_active')),
                    can_enable_arm=self.enable_blocker('enable_arm', state, now) is None,
                    can_enable_claw=self.enable_blocker('enable_claw', state, now) is None,
                    mapping_mode='relative' if state.get('mapping_mode') == 'relative' else 'absolute',
                    alignment_deg_s=num(state.get('alignment_deg_s')), message=message,
                    input_status=str(state.get('input_status') or ''), alignment_progress=str(state.get('alignment_progress') or ''),
                    limited=[str(name) for name in state.get('limited') or []],
                    loop_hz=num(state.get('loop_hz')), read_ms=num(state.get('read_ms')), joints=joints)

    def response_status(self, state):
        if state is None or not isinstance(state.get('tuning'), dict): return None
        pending = state.get('tuning_pending')
        return dict(values=response_values(state['tuning']), pending=response_values(pending),
                    startup=response_values(state.get('startup_tuning')),
                    profile=profile_index(state['tuning']), pending_profile=profile_index(pending),
                    profiles=[dict(name=name, description=description) for name, description, *_ in RESPONSE_PROFILES])

    # --- Arm dimensions and sensor axes ---------------------------------------------------------

    def configure_arm(self, message, wall):
        request_id, error = self.check(message, wall)
        if request_id is None: return
        settings = message.get('settings')
        if error is None: error = arm_settings_error(settings)
        if error is None and self.app.capture: error = 'Finish the current capture first.'
        if error is None and getattr(self.app, 'settings_dialog', None) is not None:
            error = 'Close the desktop Dimensions dialog first.'
        if error is None:
            try: self.app.apply_arm_settings(settings)
            except (ValueError, OSError) as exc: error = str(exc)
        self.arm_settings_request = dict(id=request_id, ok=error is None,
                                         detail=error or 'Settings saved. Capture pose A to start calibration.')
        self.next_publish = 0.

    def arm_settings_status(self):
        app = self.app
        settings = app.model.settings
        return dict(values={**{key: num(settings[key], 3) for key in (*ARM_LENGTHS, *ARM_POSITIONS)},
                            'axes': {role: str(settings['axes'][role]) for role in ROLES}},
                    editable=not app.capture and getattr(app, 'settings_dialog', None) is None,
                    request=self.arm_settings_request)

    # --- Joint directions (formerly only the URDF preview's checkboxes) ------------------------

    def set_joint_direction(self, message, wall):
        request_id, error = self.check(message, wall)
        if request_id is None: return
        joint, reverse = message.get('joint'), message.get('reverse')
        state = self.robot_state()
        if error is None:
            if joint not in JOINTS or type(reverse) is not bool: error = 'Choose a joint and a direction.'
            elif self.app.capture: error = 'Finish the current capture first.'
            elif state is not None and (state.get('enabled') or state.get('claw_enabled') or state.get('grip_hold_active')):
                error = 'Stop/hold the robot before changing a joint direction.'
            else: self.app.set_reverse(joint, reverse)
        detail = error or f"{JOINT_NAMES[joint]}: {'reversed' if reverse else 'normal'} direction. Re-enable the arm when ready."
        self.directions_request = dict(id=request_id, ok=error is None, detail=detail)
        self.next_publish = 0.

    def directions_status(self, state):
        reverse = getattr(self.app, 'reverse', None)
        if not isinstance(reverse, dict): return None
        active = state is not None and bool(state.get('enabled') or state.get('claw_enabled') or state.get('grip_hold_active'))
        return dict(reverse={name: bool(reverse.get(name, False)) for name in JOINTS},
                    editable=not self.app.capture and not active, request=self.directions_request)

    # --- Haptic coin bench -------------------------------------------------------------------

    def test_haptic_motor(self, message, wall):
        action = message.get('action')
        # Starting a test must not survive a Stop; adjusting or stopping a coin may.
        request_id, error = self.check(message, wall, revision=action in ('test', 'run'))
        if request_id is None: return
        motor, percent = message.get('motor'), message.get('percent')
        if error is None:
            if action not in ('test', 'run', 'intensity', 'stop'): error = 'Unknown coin action.'
            elif type(motor) is not int or not 0 <= motor < len(MOTORS): error = 'Choose one of the four coins.'
            elif action != 'stop' and (not is_number(percent) or not 0 <= percent <= 100): error = 'Intensity must be 0-100%.'
            elif action in ('test', 'run') and self.app.capture: error = 'Finish calibration before testing coins.'
            else:
                board, index = MOTORS[motor]
                if action == 'stop': self.app.hub.stop(board, index)
                elif action == 'intensity': self.app.set_motor_percent(board, index, float(percent))
                else: error = self.app.run_motor(board, index, 1 if action == 'test' else 10, float(percent))
        detail = error or {'test': 'Coin test started (1 s).', 'run': 'Coin running for 10 s.',
                           'intensity': 'Intensity set.', 'stop': 'Coin stopped.'}[action]
        self.coins_request = dict(id=request_id, ok=error is None, detail=detail)
        self.next_publish = 0.

    def coins_status(self, snapshot, now):
        app = self.app
        percents = getattr(app, 'motor_percent', None) or {}
        motors = []
        for board_name, index in MOTORS:
            board = snapshot[board_name]
            state = board['motors'][index]
            fields = board.get('fields') or {}
            available = bool(board['connected'] and is_number(board.get('status_at')) and now-board['status_at'] < 6
                             and fields.get('DRV2605L' if index == 1 else 'PWM2') == '1')
            feedback = bool(state.get('feedback'))
            message = str(board.get('message') or '')
            if not available: detail = driver_status(board, now) if index == 1 else 'PWM unavailable / no recent status'
            elif 'stopped:' in message or message.startswith('ERROR'): detail = message
            else: detail = 'Driver detected at 0x5A' if index == 1 else 'PWM ready · motor presence not detectable'
            output = state.get('percent') or 0
            running = max(0., state['until']-now) if output and not feedback and is_number(state.get('until')) else 0.
            motors.append(dict(available=available, percent=num(percents.get((board_name, index), 40.)), output=num(output),
                               running_s=num(running), acks=int(state.get('acks') or 0), feedback=feedback, detail=detail))
        return dict(motors=motors, editable=not app.capture, request=self.coins_request)

    # --- Sensor health and the 3-IMU arm ----------------------------------------------------------

    def wearable_status(self, snapshot, now):
        imus = []
        for sensor in ROLES.values():
            sample = snapshot['wroom' if sensor == 'wroom' else 's3']['samples'][sensor]
            age = now-sample['seen'] if sample['q'] is not None and is_number(sample.get('seen')) else None
            live = age is not None and 0 <= age < .4
            times = sample.get('times') or ()
            hz = (len(times)-1)/(times[-1]-times[0]) if live and len(times) > 1 and times[-1] > times[0] else 0.
            imus.append(dict(live=live, hz=num(hz), age_ms=num(age*1000, 0) if age is not None else None))
        boards = [dict(name=name, port=str(snapshot[name].get('port') or ''), connected=bool(snapshot[name]['connected']),
                       driver=driver_status(snapshot[name], now), message=str(snapshot[name].get('message') or ''))
                  for name in ('wroom', 's3')]
        calibration = self.app.joystick_calibration
        joystick, stick = snapshot['wroom'].get('joystick'), None
        if joystick and is_number(joystick.get('seen')):
            axes = calibration.normalized(joystick['x'], joystick['y'])
            stick = dict(x=int(joystick['x']), y=int(joystick['y']), nx=num(axes['x'], 3), ny=num(axes['y'], 3),
                         pressed=bool(joystick['pressed']), fresh=0 <= now-joystick['seen'] < .4)
        return dict(imus=imus, boards=boards, joystick=stick,
                    joystick_center=dict(x=num(calibration.center_x), y=num(calibration.center_y),
                                         deadzone=num(calibration.deadzone, 3)))

    def geometry_status(self):
        geometry = self.app.geometry
        point = lambda p: [num(v, 3) for v in p]
        return dict(joints=[point(p) for p in geometry['joints']],
                    markers=[point(geometry['markers'][role]) for role in ROLES],
                    boards=[[point(axis) for axis in geometry['boards'][role]] for role in ROLES],
                    palm_side=point(geometry['bases']['hand'][1]), elbow_deg=num(geometry['elbow_deg']),
                    wrist_deg=num(geometry['wrist_deg']), calibrated=bool(self.app.model.calibrated))

    # --- Transport ----------------------------------------------------------------------------

    def poll(self):
        # Bound work per UI tick, including unrelated vision/haptics commands.
        for _ in range(16):
            try:
                frame = self.subscriber.recv(zmq.NOBLOCK)
            except zmq.Again:
                break
            try:
                message = json.loads(frame[len(b'command '):])
            except (ValueError, UnicodeError):
                continue
            try:
                self.command(message)
            except Exception:
                # Never let one bad command reach the Tk tick, which would pause the robot.
                LOG.exception('Web command failed')

    def block(self, name, function, *args):
        """One status section; a failure drops only that section and is logged once."""
        try:
            return function(*args)
        except Exception as error:
            key = (name, f'{type(error).__name__}: {error}')
            if key not in self.block_errors:
                if len(self.block_errors) > 64: self.block_errors.clear()
                self.block_errors.add(key)
                LOG.exception('Web status section %s unavailable', name)
            return None

    def status(self, snapshot, now, wall=None):
        app = self.app
        try:
            fresh_frame(snapshot, now)
            input_fresh = True
            detail = app.message.get()
        except ValueError as error:
            input_fresh = False
            detail = str(error)
        sensors = []
        for sensor in ROLES.values():
            board = snapshot['wroom' if sensor == 'wroom' else 's3']
            sample = board['samples'][sensor]
            sensors.append(bool(board['connected'] and sample['q'] is not None and 0 <= now - sample['seen'] < .3))
        capture = app.capture
        remaining, pose = 0., None
        if capture:
            elapsed = max(0., now - capture['start'])
            phase = 'preparing' if elapsed < 3 else 'sampling'
            remaining = max(0., (3 if elapsed < 3 else 5) - elapsed)
            pose = capture['pose']
        elif app.model.calibrated:
            phase = 'ready'
        elif app.model.mounts is not None:
            phase = 'needs_recenter'
        elif app.pose_a is not None:
            phase = 'needs_pose_b'
        else:
            phase = 'needs_pose_a'
        state = self.robot_state()
        block = self.block
        return dict(t=time.time() if wall is None else wall, session=self.session, revision=self.revision,
                    phase=phase, capture_pose=pose, remaining_s=round(remaining, 2),
                    input_fresh=input_fresh, sensors=sensors, can_recenter=app.model.mounts is not None,
                    detail=detail, request=self.request, motion_ranges=dict(app.model.motion_ranges),range_capture=True,pan_capture=True,
                    saved_config=getattr(app,'saved_config',None),
                    tuning=block('tuning', self.tuning_status, now, state), slider=block('slider', self.slider_status, state),
                    replay=block('replay', self.replay_status, state),
                    control_request=block('control_request', self.control_status, state),
                    physical=block('physical', self.physical_status, state, now),
                    response=block('response', self.response_status, state),
                    arm_settings=block('arm_settings', self.arm_settings_status),
                    directions=block('directions', self.directions_status, state),
                    coins=block('coins', self.coins_status, snapshot, now),
                    wearable=block('wearable', self.wearable_status, snapshot, now),
                    geometry=block('geometry', self.geometry_status))

    def publish(self, snapshot, now):
        if now < self.next_publish:
            return
        self.next_publish = now + .1
        try:
            self.publisher.send_string('calibration ' + json.dumps(self.status(snapshot, now),
                                       separators=(',', ':'), allow_nan=False), zmq.NOBLOCK)
            self.publish_error = None
        except zmq.Again:
            pass
        except Exception as error:
            detail = f'{type(error).__name__}: {error}'
            if detail != self.publish_error:  # Log each distinct failure once, not at 10 Hz.
                self.publish_error = detail
                LOG.exception('Calibration status unavailable')

    def close(self):
        self.subscriber.close(linger=0)
        self.publisher.close(linger=0)
        self.context.term()
