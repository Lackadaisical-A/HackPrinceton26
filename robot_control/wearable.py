"""Physical SO-101 follower for the calibrated Wearable Lab geometry.

Only the worker thread owns COM6. UI updates replace a single latest input;
there is no motion queue. Connecting is read-only; motion needs manual enable.
"""
from copy import deepcopy
import json
import logging
import math
from pathlib import Path
import queue
import threading
import time

from robot_control.arm import Arm,BODY,JOINTS
from robot_control.joystick import JoystickClaw
from wearable_test.retarget import Retargeter

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
    for name,upper in (("speed_deg_s",60),("max_lead_deg",5),("max_load",1023)):
        if not isinstance(config[name],(int,float)) or not math.isfinite(config[name]) or not 0<config[name]<=upper:
            raise ValueError(f"Invalid physical robot setting: {name}")
    if set(config["raw_direction"])!=set(BODY) or any(v not in (-1,1) for v in config["raw_direction"].values()):
        raise ValueError("raw_direction must contain +1 or -1 for each arm joint")
    return config


def bounded_follow(anchor,source_anchor,source,previous,measured,limits,dt,config):
    """Anchor at enable, then follow URDF-angle differences without a position jump."""
    if not math.isfinite(dt) or dt<0: raise ValueError("invalid control timing")
    step = math.radians(config["speed_deg_s"])*TICKS_PER_RADIAN*min(.1,dt)
    lead = math.radians(config["max_lead_deg"])*TICKS_PER_RADIAN
    result = {}
    for name in BODY:
        values = (anchor[name],source_anchor[name],source[name],previous[name],measured[name],*limits[name])
        if not all(math.isfinite(v) for v in values): raise ValueError(f"invalid {name} telemetry")
        low,high = limits[name]
        if not low<=measured[name]<=high: raise ValueError(f"{name} outside calibrated range")
        if abs(previous[name]-measured[name])>max(lead*2,1):
            raise ValueError(f"{name} tracking error; arm did not follow its previous target")
        target = anchor[name]+config["raw_direction"][name]*(source[name]-source_anchor[name])*TICKS_PER_RADIAN
        target = max(previous[name]-step,min(previous[name]+step,target))
        target = max(measured[name]-lead,min(measured[name]+lead,target))
        result[name] = max(low,min(high,target))
    return result


class WearableRobot:
    def __init__(self,port,joystick_calibration,arm_factory=Arm,clock=time.monotonic,config=None):
        self.port,self.arm_factory,self.clock = port,arm_factory,clock
        self.config = config or load_config()
        self.claw = JoystickClaw(calibration=joystick_calibration)
        self.retarget = Retargeter(URDF)
        self.mapping_epoch = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.commands = queue.Queue()
        self.packet = None
        self.generation = 0
        self.pause_pending = None
        self.enabled = False
        self.active_epoch = None
        self.status = dict(connected=False,enabled=False,claw_enabled=False,can_enable=False,
                           message=f"Connecting to {port} (read only)",seen=None,positions={},loads={},torque={},targets={})
        self.thread = threading.Thread(target=self.run,daemon=True,name="Physical SO101 wearable controller")

    def start(self): self.thread.start()

    def submit(self,packet):
        with self.lock: self.packet = deepcopy(packet)

    def request(self,command):
        if command not in ("enable_arm","enable_claw"): raise ValueError("Unknown robot command")
        with self.lock: generation = self.generation
        self.commands.put((command,generation,self.clock()))

    def pause(self,reason="operator pause"):
        with self.lock:
            self.generation += 1  # Invalidates queued enables, including one being checked.
            self.pause_pending = reason

    def snapshot(self):
        with self.lock: return deepcopy(self.status)

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
        if any(not math.isfinite(packet[key]) or not 0<=now-packet[key]<.3 for key in ("seen","published")):
            raise ValueError("wearable data or UI heartbeat stale")
        if epoch is not None and packet["epoch"]!=epoch:
            raise ValueError("sensor/reference/direction changed; re-enable from the current pose")
        self.claw.fresh(packet["joystick"],now)  # Hand switch stops arm as well as claw.
        return packet

    def source_target(self,packet):
        if packet["epoch"]!=self.mapping_epoch:
            self.retarget = Retargeter(URDF)
            self.mapping_epoch = packet["epoch"]
        return self.retarget.targets(packet["geometry"],packet["reverse"])

    def pause_motion(self,arm,reason):
        had_control = self.enabled or self.claw.enabled
        self.enabled = False
        self.claw.pause(reason)
        if had_control: arm.hold_enabled()
        self.note("PAUSED: "+reason+". Manual enable required.")

    def cycle(self,arm,dt):
        # No cached telemetry may authorize a motion write.
        telemetry_started = self.clock()
        positions = arm.bus.sync_read("Present_Position",list(JOINTS),normalize=False,num_retry=0)
        loads = arm.bus.sync_read("Present_Load",list(JOINTS),normalize=False,num_retry=0)
        torque = arm.bus.sync_read("Torque_Enable",list(JOINTS),normalize=False,num_retry=0)
        if any(set(values)!=set(JOINTS) or not all(math.isfinite(v) for v in values.values())
               for values in (positions,loads,torque)):
            raise ValueError("invalid robot telemetry")
        with self.lock:
            generation = self.generation
            pause = self.pause_pending
            self.pause_pending = None
        if pause: self.pause_motion(arm,pause)
        try:
            self.checked_packet()
            can_enable = True
        except (ValueError,KeyError,TypeError): can_enable = False
        new_arm = new_claw = False
        while True:
            try: command,requested_generation,requested_at = self.commands.get_nowait()
            except queue.Empty: break
            if requested_generation!=generation or self.clock()-requested_at>.5: continue
            try:
                packet = self.checked_packet(generation)
                if command=="enable_arm":
                    if self.enabled: continue
                    if any(abs(loads[n])>=self.config["max_load"] for n in BODY): raise ValueError("high arm load")
                    arm.validate_motion()
                    measured = arm.positions()
                    packet = self.checked_packet(generation,packet["epoch"])
                    source = self.source_target(packet)
                    self.anchor,self.source_anchor,self.previous = dict(measured),dict(source),dict(measured)
                    self.limits = {n:arm.limits(n) for n in BODY}
                    # Validate the final measured pose before the first write.
                    bounded_follow(measured,source,source,measured,measured,self.limits,0,self.config)
                    self.checked_packet(generation,packet["epoch"])
                    arm.latch_and_enable(measured)
                    self.enabled = True
                    self.active_epoch = packet["epoch"]
                    self.active_generation = generation
                    new_arm = True
                    self.note(f"LIVE arm anchored here ({self.config['speed_deg_s']:g} deg/s)")
                else:
                    if self.claw.enabled: continue
                    if abs(loads["gripper"])>=self.claw.load_limit: raise ValueError("high claw load")
                    measured = arm.validate_gripper()
                    packet = self.checked_packet(generation,packet["epoch"])
                    self.claw.reverse = packet["reverse"]["gripper"]
                    self.claw.enable(packet["joystick"],measured,arm.limits("gripper"),self.clock())
                    self.checked_packet(generation,packet["epoch"])
                    arm.latch_gripper_and_enable(measured)
                    self.claw_epoch,self.claw_generation = packet["epoch"],generation
                    new_claw = True
                    self.note("Claw enabled: Y=0 open, Y=4095 closed; neutral halfway")
            except (ValueError,KeyError,TypeError) as error:
                self.pause_motion(arm,f"Enable refused: {error}")
        targets = {}
        try:
            if self.enabled and not new_arm:
                if self.clock()-telemetry_started>=.25: raise ValueError("robot telemetry too slow/stale")
                packet = self.checked_packet(self.active_generation,self.active_epoch)
                if any(abs(loads[n])>=self.config["max_load"] for n in BODY): raise ValueError("high arm load")
                if not all(torque[n] for n in BODY):
                    raise ValueError("arm torque switched off")
                target = bounded_follow(self.anchor,self.source_anchor,self.source_target(packet),self.previous,
                                        positions,self.limits,dt,self.config)
                self.checked_packet(self.active_generation,self.active_epoch)
                arm.move_raw(target)
                self.previous = target
                targets.update(target)
            if self.claw.enabled and not new_claw:
                if self.clock()-telemetry_started>=.25: raise ValueError("robot telemetry too slow/stale")
                packet = self.checked_packet(self.claw_generation,self.claw_epoch)
                if not torque["gripper"]: raise ValueError("claw torque switched off")
                target = self.claw.step(packet["joystick"],positions["gripper"],loads["gripper"],dt,self.clock())
                self.checked_packet(self.claw_generation,self.claw_epoch)
                arm.move_gripper(target)
                targets["gripper"] = target
        except (ValueError,KeyError,TypeError) as error:
            self.pause_motion(arm,str(error))
            targets = {}
        with self.lock:
            self.status.update(connected=True,enabled=self.enabled,claw_enabled=self.claw.enabled,can_enable=can_enable,
                               seen=self.clock(),positions=positions,loads=loads,torque=torque,targets=targets)

    def run(self):
        arm = None
        try:
            arm = self.arm_factory(self.port)
            arm.connect()
            arm.bus.set_timeout(150)
            self.note(f"{self.port}: connected, read only. Calibrate, then enable arm/claw.")
            previous = self.clock()
            while not self.closed.is_set():
                start = self.clock()
                self.cycle(arm,start-previous)
                previous = start
                self.closed.wait(max(.001,.05-(self.clock()-start)))
        except Exception as error:
            logging.getLogger("wearable").exception("Physical robot connection/control stopped")
            self.note(f"OFF: {type(error).__name__}: {error}. Reconnect/restart required.")
        finally:
            self.enabled = False
            self.claw.pause("controller stopped")
            if arm is not None:
                try: arm.hold_enabled()
                except Exception: logging.getLogger("wearable").exception("Could not send robot hold")
                try: arm.close()
                except Exception: logging.getLogger("wearable").exception("Could not close robot port")
            with self.lock: self.status.update(connected=False,enabled=False,claw_enabled=False,can_enable=False)

    def close(self):
        self.pause("window closed")
        self.closed.set()
        if self.thread.is_alive(): self.thread.join(timeout=3)
