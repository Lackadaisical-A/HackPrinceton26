"""One worker owns the arm bus; the dashboard remains the only ESP32 owner."""
import json
import math
from pathlib import Path
import queue
import threading
import time

from robot_control.arm import Arm, BODY
from robot_control.load_feedback import LoadFeedback
from robot_control.joystick import JoystickClaw
from so101_sim.mapper import Mapper, sensor_frame

ROOT = Path(__file__).resolve().parents[1]


def bounded_step(anchor, offsets, previous, measured, limits, dt):
    """10 degrees/s, at most 4 degrees ahead of measured position, calibrated bounds."""
    step = 10*4095/360*min(.1, max(0, dt))
    lead = 4*4095/360
    result = {}
    for name in BODY:
        low, high = limits[name]
        target = anchor[name]+math.degrees(offsets[name])*4095/360
        target = max(previous[name]-step, min(previous[name]+step, target))
        target = max(measured[name]-lead, min(measured[name]+lead, target))
        result[name] = min(high, max(low, target))
    return result


class RobotWorker:
    def __init__(self, hub, port, joint="shoulder_pan", reverse=False, deadband=25, full_scale=95,
                 joystick_axis="y", reverse_claw=False):
        self.hub, self.port, self.joint = hub, port, joint
        self.feedback = LoadFeedback(deadband, full_scale, reverse=reverse)
        self.lock = threading.Lock()
        self.commands = queue.Queue()
        self.status = "Connecting to arm (read only)"
        self.motion_status = "IMU arm control OFF"
        self.claw = JoystickClaw(joystick_axis, reverse_claw, hub.joystick_calibration)
        self.latest = {}
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.config = json.loads((ROOT/"so101_sim"/"mapping.json").read_text())
        self.config["max_sample_age_s"] = .3
        # These are the URDF limits used by the preview, in radians.
        import xml.etree.ElementTree as ET
        urdf = ET.parse(ROOT/"so101_sim"/"model"/"so101_new_calib.urdf")
        self.mapper = Mapper(self.config, {
            j.attrib["name"]: (float(j.find("limit").attrib["lower"]), float(j.find("limit").attrib["upper"]))
            for j in urdf.findall("joint") if j.attrib["name"] in BODY
        })

    def frame(self):
        now = time.time_ns()//1_000_000
        data = {name: {"online": s["sequence"] > 0, "timestamp_ms": s["timestamp_ms"],
                       "hub_id": self.hub.session, "quaternion": dict(zip("wxyz", s["q"]))}
                for name, s in self.hub.snapshot().items()}
        return sensor_frame(data, self.config, now)

    def haptic_output(self, now):
        with self.lock:
            return self.feedback.output(now)

    def telemetry(self):
        with self.lock:
            return {**self.latest, "online": bool(self.feedback.seen is not None and time.monotonic()-self.feedback.seen < .3),
                    "status": self.status, "motion_status": self.motion_status,
                    "claw_enabled": self.claw.enabled, "claw_status": self.claw.status}

    def run(self):
        arm = Arm(self.port)
        enabled = False
        try:
            arm.connect()
            self.status = f"SO-101 connected on {self.port}; reading {self.joint} load"
            previous_time = time.monotonic()
            while not self.hub.stop.is_set():
                now = time.monotonic()
                dt, previous_time = now-previous_time, now
                load = arm.read_load(self.joint)
                torque = int(arm.bus.read("Torque_Enable", self.joint, normalize=False, num_retry=0))
                self.status = f"{self.port} {self.joint}: load {load:+d} | torque {'ON' if torque else 'OFF'}"
                with self.lock:
                    self.feedback.update(load, time.monotonic())
                    self.latest = {"port": self.port, "joint": self.joint, "load_raw": load,
                                   "hub_id": self.hub.session,
                                   "load_relative": self.feedback.load, "load_zero": self.feedback.baseline,
                                   "torque_enabled": bool(torque),
                                   "units": "servo_load_counts", "timestamp_ms": time.time_ns()//1_000_000}
                while not self.commands.empty():
                    command = self.commands.get_nowait()
                    if command == "zero":
                        with self.lock:
                            self.feedback.zero(load)
                    elif command == "pause":
                        enabled = False
                        self.claw.pause("paused by operator")
                        arm.hold()
                        self.motion_status = "PAUSED: holding present arm pose"
                    elif command == "hold":
                        enabled = False
                        self.claw.pause("stationary load test")
                        arm.hold_gripper()
                        try:
                            positions = arm.validate_motion()
                            arm.latch_and_enable(positions)
                            self.motion_status = "Holding current arm pose for load feedback; IMU control OFF"
                        except (ValueError, ConnectionError) as error:
                            arm.hold()
                            self.motion_status = f"Hold refused: {error}"
                    elif command == "claw_enable":
                        self.claw.pause("checking joystick center")
                        arm.hold_gripper()
                        try:
                            sample = self.hub.joystick_snapshot()
                            position = arm.validate_gripper()
                            self.claw.enable(sample, position, arm.limits("gripper"), time.monotonic())
                            arm.latch_gripper_and_enable(position)
                        except (ValueError, ConnectionError) as error:
                            self.claw.pause(str(error))
                            arm.hold_gripper()
                    elif command == "claw_pause":
                        self.claw.pause("paused by operator")
                        arm.hold_gripper()
                    elif command == "enable":
                        # Stop prior control before validating/recentering.
                        enabled = False
                        try:
                            frame = self.frame()
                            anchor = arm.validate_motion()
                            self.mapper.calibrate(frame)
                            previous = dict(anchor)
                            limits = {n: arm.limits(n) for n in BODY}
                            arm.latch_and_enable(anchor)
                            enabled = True
                            self.motion_status = "LIVE IMU control (10 deg/s); Pause holds arm"
                        except (ValueError, ConnectionError) as error:
                            self.claw.pause("arm enable failed")
                            arm.hold()
                            self.motion_status = f"Motion refused: {error}"
                if enabled:
                    try:
                        frame = self.frame()
                        measured = arm.positions()
                        loads = arm.bus.sync_read("Present_Load", list(BODY), normalize=False, num_retry=0)
                        if any(abs(v) >= 650 for v in loads.values()):
                            raise ValueError("high motor load; control paused")
                        target = bounded_step(anchor, self.mapper.targets(frame), previous, measured, limits, dt)
                        arm.move_raw(target)
                        previous = target
                    except ValueError as error:
                        enabled = False
                        self.claw.pause("arm control paused")
                        arm.hold()
                        self.motion_status = f"PAUSED: {error}; recapture neutral to resume"
                if self.claw.enabled:
                    try:
                        sample = self.hub.joystick_snapshot()
                        measured = arm.gripper_position()
                        claw_load = arm.read_load("gripper")
                        target = self.claw.step(sample, measured, claw_load, dt, time.monotonic())
                        arm.move_gripper(target)
                        with self.lock:
                            self.latest["gripper_position_raw"] = measured
                            self.latest["gripper_target_raw"] = target
                            self.latest["gripper_load_raw"] = claw_load
                    except ValueError as error:
                        self.claw.pause(str(error))
                        arm.hold_gripper()
                self.hub.stop.wait(.03)
        except Exception as error:
            self.status = f"Robot stopped: {type(error).__name__}: {error}"
            self.motion_status = "OFF: reconnect/restart required"
        finally:
            self.claw.pause("controller stopped")
            with self.lock:
                self.feedback.reset()
            try:
                arm.hold()
            except Exception:
                pass
            arm.close()
