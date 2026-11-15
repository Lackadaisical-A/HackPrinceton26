"""Directional taps plus servo-effort feedback through the existing serial owners.

This worker has no robot write API: neither a vision frame nor any command it
receives can enable arm motion (arm enable is Wearable Lab's console, not this). Missing ACKs, Stop, stale sensors and UI stalls disarm feedback.
"""
from copy import deepcopy
import json
import logging
import math
import threading
import time

import zmq

MOTORS = (('wroom',1), ('wroom',2), ('s3',2), ('s3',1))
NAMES = ('Palm left / DRV2605L', 'Palm right / transistor',
         'Forearm left / transistor', 'Forearm right / DRV2605L')
DEFAULTS = dict(cap=65., guidance=45., deadzone=.12, pulse_s=.18, period_s=.65,
                grip_deadband=25., grip_full=250., arm_deadband=90., arm_full=500.)


def fresh(stamp, now, seconds):
    return isinstance(stamp,(int,float)) and not isinstance(stamp,bool) and math.isfinite(stamp) and 0 <= now-stamp < seconds


def pattern(vision, robot, *, now, wall, started, config=DEFAULTS):
    """Wire order: palm L/R, forearm L/R. Image +x is right; +y is down."""
    levels = [0.,0.,0.,0.]
    resistance = False
    if robot.get('connected') and fresh(robot.get('seen'),now,.25):
        loads = robot.get('loads',{})
        if len(loads) == 6 and all(isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<=1023 for v in loads.values()):
            def effort(value,low,high):
                return 0. if value <= low else 28.+17.*min(1.,(value-low)/(high-low))
            grip = effort(abs(loads.get('gripper',0)),config['grip_deadband'],config['grip_full'])
            arm = effort(max(abs(v) for n,v in loads.items() if n!='gripper'),config['arm_deadband'],config['arm_full'])
            levels = [grip,grip,arm,arm]
            resistance = max(levels)>0
    direction = ''
    if (isinstance(vision,dict) and vision.get('status')=='ok' and isinstance(vision.get('target'),dict)
            and fresh(vision.get('t'),wall,.5)):
        g = vision.get('guidance')
        if isinstance(g,dict) and g.get('aligned') is False:
            dx,dy = g.get('dx'),g.get('dy')
            if all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and abs(v)<=1 for v in (dx,dy)):
                x = dx if abs(dx)>config['deadzone'] else 0.
                y = dy if abs(dy)>config['deadzone'] else 0.
                direction = ' + '.join(filter(None,('left' if x<0 else 'right' if x>0 else '',
                                                     'up / forearm' if y<0 else 'down / palm' if y>0 else '')))
                if direction and (now-started)%config['period_s'] < config['pulse_s']:
                    scale = abs(x)+abs(y)
                    weights = [max(0.,v)/scale for v in (-x+y,x+y,-x-y,x-y)]
                    for i,w in enumerate(weights):
                        if w>.15: levels[i] += max(28.,config['guidance']*w)
    cue = 'resistance' if resistance else 'guide' if direction else None
    detail = ('Taps: '+direction if direction else 'No directional cue')
    if resistance: detail += ' | Steady: servo resistance'
    return [min(config['cap'],v) for v in levels],cue,detail


class HapticFeedback:
    def __init__(self, hub, robot, vision_endpoint='tcp://127.0.0.1:5555', commands_endpoint='tcp://127.0.0.1:5557',
                 publish_endpoint='tcp://127.0.0.1:5561'):
        self.hub,self.robot = hub,robot
        self.vision_endpoint,self.commands_endpoint = vision_endpoint,commands_endpoint
        self.publish_endpoint = publish_endpoint
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.enabled = False
        self.token = None
        self.vision = None
        self.ui_seen = time.monotonic()
        self.started = self.ui_seen
        self.disabled_at = time.time()
        self.state = dict(t=time.time(),enabled=False,motors=[0.,0.,0.,0.],cue=None,
                          detail='Feedback off. Enable guidance + resistance.',acknowledged=[False]*4)
        self.thread = threading.Thread(target=self.run,daemon=True,name='Wearable guidance and resistance')

    def start(self): self.thread.start()

    def heartbeat(self):
        with self.lock: self.ui_seen = time.monotonic()

    def snapshot(self):
        with self.lock: return deepcopy(self.state)

    def stop(self, reason='operator stop'):
        with self.lock:
            self.enabled = False
            self.token = None
            self.disabled_at = time.time()
            self.hub.stop()
            self.state.update(t=time.time(),enabled=False,motors=[0.]*4,cue=None,
                              detail='Feedback off: '+reason,acknowledged=[False]*4)

    def enable(self, command_time=None):
        with self.lock:
            if command_time is not None and command_time <= self.disabled_at:
                return  # Never let a queued pre-Stop enable undo a local Stop.
            if self.enabled: return
            try:
                if self.closed.is_set() or not fresh(self.ui_seen,time.monotonic(),.5):
                    raise ValueError('Wearable UI is not responding')
                self.token = self.hub.begin_feedback()
                self.started = time.monotonic()
                self.enabled = True
                self.state.update(t=time.time(),enabled=True,detail='Guidance + resistance enabled')
            except ValueError as error:
                self.stop(str(error))

    def command(self, message, wall=None):
        wall = time.time() if wall is None else wall
        if (isinstance(message,dict) and message.get('type')=='set_haptics'
                and isinstance(message.get('enabled'),bool) and fresh(message.get('t'),wall,2.)):
            if message['enabled']: self.enable(message['t'])
            else: self.stop('console switch')

    def tick(self):
        with self.lock:
            now,wall = time.monotonic(),time.time()
            if not self.enabled:
                self.state['t'] = wall
                return
            try:
                if not fresh(self.ui_seen,now,.5): raise ValueError('Wearable UI stopped responding')
                robot = self.robot.snapshot()
                levels,cue,detail = pattern(self.vision,robot,now=now,wall=wall,started=self.started)
                if not robot['connected'] or not fresh(robot['seen'],now,.25):
                    detail += ' | Robot offline: resistance unavailable'
                self.hub.feedback(levels,self.token)
                boards = self.hub.snapshot()
                acknowledged = [bool(boards[b]['motors'][m]['acks'] and boards[b]['motors'][m]['running']) for b,m in MOTORS]
                self.state.update(t=wall,enabled=True,motors=[v/100 for v in levels],cue=cue,
                                  detail=detail,acknowledged=acknowledged)
            except (ValueError,KeyError,TypeError) as error:
                self.stop(str(error))

    def run(self):
        context = zmq.Context()
        sockets = []
        publisher = None
        try:
            publisher = context.socket(zmq.PUB)
            publisher.setsockopt(zmq.LINGER,0)
            publisher.setsockopt(zmq.SNDHWM,2)
            publisher.bind(self.publish_endpoint)
            for endpoint,topic in ((self.vision_endpoint,'vision '),(self.commands_endpoint,'command ')):
                sock = context.socket(zmq.SUB)
                sockets.append((sock,topic))
                sock.setsockopt(zmq.LINGER,0)
                sock.setsockopt(zmq.RCVHWM,8)
                sock.setsockopt(zmq.MAXMSGSIZE,65536)
                sock.setsockopt_string(zmq.SUBSCRIBE,topic)
                sock.connect(endpoint)
            while not self.closed.is_set():
                for sock,topic in sockets:
                    for _ in range(32):
                        try: frame = sock.recv_string(zmq.NOBLOCK)
                        except zmq.Again: break
                        try: message = json.loads(frame[len(topic):])
                        except (ValueError,UnicodeError): continue
                        if topic=='command ': self.command(message)
                        else:
                            with self.lock: self.vision = message
                self.tick()
                try:
                    publisher.send_string('haptics '+json.dumps(self.snapshot(),separators=(',',':'),allow_nan=False),zmq.NOBLOCK)
                except zmq.Again: pass
                self.closed.wait(.04)
        except Exception:
            logging.getLogger('wearable').exception('Vibration feedback stopped')
        finally:
            self.stop('feedback worker closed')
            self.closed.set()
            for sock,_ in sockets: sock.close(linger=0)
            if publisher is not None: publisher.close(linger=0)
            context.term()

    def close(self):
        self.stop('window closed')
        self.closed.set()
        if self.thread.is_alive(): self.thread.join(timeout=2)
