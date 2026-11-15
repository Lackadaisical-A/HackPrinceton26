"""Physical SO-101 follower for the calibrated Wearable Lab geometry.

Only the worker thread owns COM6. UI updates replace a single latest input;
there is no motion queue. Connecting is read-only; motion needs manual enable.

A stick tap starts/stops recording the motion; holding the stick pauses. A
recorded motion is replayed on request inside the same rate/lead/load bounds.
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
from robot_control.motion_replay import Replay,StickButton,duration,load_latest,save_motion,valid_samples
from wearable_test.retarget import Retargeter

HERE = Path(__file__).resolve().parent
URDF = HERE.parent/"so101_sim/model/so101_new_calib.urdf"
TICKS_PER_RADIAN = 4095/(2*math.pi)  # Matches the vendored LeRobot degree convention.
MIN_RECORD_S,MAX_RECORD_S = .2,120


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
    def __init__(self,port,joystick_calibration,arm_factory=Arm,clock=time.monotonic,config=None,
                 motion_dir=HERE.parent/"recordings"):
        self.port,self.arm_factory,self.clock = port,arm_factory,clock
        self.config = config or load_config()
        # The stick button is read here: a tap records, a hold pauses.
        self.claw = JoystickClaw(calibration=joystick_calibration,button_stops=False)
        self.button = StickButton()
        self.taps = 0
        self.motion_dir = Path(motion_dir)
        self.motion,loaded = load_latest(self.motion_dir)
        logging.getLogger("wearable").info("Physical robot motion: %s",loaded)
        self.recording = None  # (start time, samples) between two stick taps.
        self.replay = None
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
                           message=f"Connecting to {port} (read only)",seen=None,positions={},loads={},torque={},targets={},
                           **self.motion_status())
        self.thread = threading.Thread(target=self.run,daemon=True,name="Physical SO101 wearable controller")

    def start(self): self.thread.start()

    def submit(self,packet):
        # The UI submits every 20 ms, fast enough to see a short tap; the 50 ms worker cycle is not.
        stop = None
        with self.lock:
            self.packet = deepcopy(packet)
            event = self.button.update(packet["joystick"],self.clock())
            if event=="tap": self.taps += 1
            elif event=="hold": stop = "joystick switch held"
            if self.replay is not None and self.button.pressed:
                self.button.consume()  # This press stops the replay; it must not also start a recording.
                stop = "stick pressed: replay stopped"
        if stop: self.pause(stop)

    def request(self,command):
        if command not in ("enable_arm","enable_claw","replay"): raise ValueError("Unknown robot command")
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
        self.claw.fresh(packet["joystick"],now)  # A lost hand joystick stops arm as well as claw.
        return packet

    def ui_alive(self,generation):
        """Replay needs no wearable pose, but Stop/Esc and the hand stick must still be able to reach it."""
        now = self.clock()
        with self.lock:
            packet,current_generation = self.packet,self.generation
        if self.closed.is_set(): raise ValueError("controller is closing")
        if generation!=current_generation: raise ValueError("operator pause")
        if packet is None or not math.isfinite(packet["published"]) or not 0<=now-packet["published"]<.3:
            raise ValueError("UI heartbeat stale")
        self.claw.fresh(packet["joystick"],now)  # As in live control: no motion without the hand-side stop.

    def source_target(self,packet):
        if packet["epoch"]!=self.mapping_epoch:
            self.retarget = Retargeter(URDF)
            self.mapping_epoch = packet["epoch"]
        return self.retarget.targets(packet["geometry"],packet["reverse"])

    def pause_motion(self,arm,reason):
        had_control = self.enabled or self.claw.enabled or self.replay is not None
        self.enabled = False
        self.claw.pause(reason)
        with self.lock: self.replay = None
        if had_control: arm.hold_enabled()
        self.note("PAUSED: "+reason+". Manual enable required.")

    def motion_status(self):
        return dict(recording=None if self.recording is None else self.clock()-self.recording[0],
                    replay=None if self.replay is None else (self.replay.phase,self.replay.elapsed,self.replay.duration),
                    motion=None if self.motion is None else duration(self.motion))

    def stick_tapped(self,arm):
        if self.replay is not None: self.pause_motion(arm,"stick pressed: replay stopped")
        elif self.recording is None:
            self.recording = (self.clock(),[])
            self.note("RECORDING the robot motion: tap the stick again to stop")
        else: self.note(self.finish_recording())

    def finish_recording(self):
        """Keep the take as the motion to replay and save it. Returns what happened."""
        samples,self.recording = self.recording[1],None
        try:
            if valid_samples(samples) and duration(samples)<MIN_RECORD_S: raise ValueError("too short")
        except ValueError as error: return f"Recording discarded: {error}"
        self.motion = samples
        try: saved = "saved as "+save_motion(self.motion_dir,samples).name
        except OSError as error: saved = f"NOT saved to disk ({error})"
        return f"Recorded {duration(samples):.1f} s; {saved}. Replay last motion plays it."

    def start_replay(self,arm,loads,generation):
        if self.motion is None: raise ValueError("no recorded motion; tap the stick to record one")
        if self.recording is not None: raise ValueError("stop the recording first")
        self.ui_alive(generation)
        if any(abs(loads[n])>=self.config["max_load"] for n in BODY) or abs(loads["gripper"])>=self.claw.load_limit:
            raise ValueError("high load")
        measured = {**arm.validate_motion(),"gripper":arm.validate_gripper()}
        limits = {n:arm.limits(n) for n in JOINTS}
        travel = limits["gripper"][1]-limits["gripper"][0]
        # The same bounds as live control: arm from wearable_config.json, gripper from the claw.
        rate = dict.fromkeys(BODY,math.radians(self.config["speed_deg_s"])*TICKS_PER_RADIAN)
        lead = dict.fromkeys(BODY,math.radians(self.config["max_lead_deg"])*TICKS_PER_RADIAN)
        rate["gripper"],lead["gripper"] = self.claw.speed*travel,self.claw.max_lead*travel
        replay = Replay(self.motion,measured,limits,rate,lead)
        self.ui_alive(generation)
        # Take over from live control at the measured pose, so nothing jumps.
        self.enabled = False
        self.claw.pause("replaying a recorded motion")
        with self.lock: self.replay = replay  # From here a refusal holds every powered joint.
        self.replay_generation = generation
        arm.latch_and_enable({n:measured[n] for n in BODY})
        arm.latch_gripper_and_enable(measured["gripper"])
        self.note("REPLAY: moving to the recorded start pose. Stick press, Esc or STOP aborts.")

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
            taps,self.taps = self.taps,0
        if pause: self.pause_motion(arm,pause)
        for _ in range(taps): self.stick_tapped(arm)
        try:
            self.checked_packet()
            can_enable = True
        except (ValueError,KeyError,TypeError): can_enable = False
        new_arm = new_claw = new_replay = False
        while True:
            try: command,requested_generation,requested_at = self.commands.get_nowait()
            except queue.Empty: break
            if requested_generation!=generation or self.clock()-requested_at>.5: continue
            if self.replay is not None: continue  # Nothing else takes the arm during a replay.
            try:
                if command=="replay":  # Needs no wearable input, so no checked_packet.
                    self.start_replay(arm,loads,generation)
                    new_replay = True
                    continue
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
                self.pause_motion(arm,f"{'Replay' if command=='replay' else 'Enable'} refused: {error}")
        targets = {}
        try:
            if self.replay is not None and not new_replay:
                if self.clock()-telemetry_started>=.25: raise ValueError("robot telemetry too slow/stale")
                self.ui_alive(self.replay_generation)
                if any(abs(loads[n])>=self.config["max_load"] for n in BODY) or abs(loads["gripper"])>=self.claw.load_limit:
                    raise ValueError("high load during replay")
                if not all(torque[n] for n in JOINTS): raise ValueError("torque switched off during replay")
                target,done = self.replay.step(positions,dt)
                self.ui_alive(self.replay_generation)
                arm.move_raw({n:target[n] for n in BODY})
                arm.move_gripper(target["gripper"])
                targets.update(target)
                if done:
                    with self.lock: self.replay = None
                    self.note("Replay finished: holding the final pose. Enable arm/claw for live control.")
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
        if self.recording is not None:
            # The commanded target where a joint is under control (keeps the same squeeze
            # and sag on replay), the measured position where it is not (hand-guided).
            started,samples = self.recording
            elapsed = self.clock()-started
            samples.append([round(elapsed,3),*(round(min(4095.,max(0.,float(targets.get(n,positions[n])))),1) for n in JOINTS)])
            if elapsed>=MAX_RECORD_S: self.note(self.finish_recording()+f" Stopped at the {MAX_RECORD_S} s limit.")
        with self.lock:
            self.status.update(connected=True,enabled=self.enabled,claw_enabled=self.claw.enabled,can_enable=can_enable,
                               seen=self.clock(),positions=positions,loads=loads,torque=torque,targets=targets,
                               **self.motion_status())

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
            with self.lock: self.replay = None
            if arm is not None:
                try: arm.hold_enabled()
                except Exception: logging.getLogger("wearable").exception("Could not send robot hold")
                try: arm.close()
                except Exception: logging.getLogger("wearable").exception("Could not close robot port")
            if self.recording is not None:  # Keep the take; the status message keeps the reason for stopping.
                try: logging.getLogger("wearable").info("Physical robot: %s",self.finish_recording())
                except Exception: logging.getLogger("wearable").exception("Could not save the recording")
            with self.lock:
                self.status.update(connected=False,enabled=False,claw_enabled=False,can_enable=False,**self.motion_status())

    def close(self):
        self.pause("window closed")
        self.closed.set()
        if self.thread.is_alive(): self.thread.join(timeout=3)
