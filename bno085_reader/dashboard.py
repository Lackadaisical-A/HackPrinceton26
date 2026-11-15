# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5", "firebase-admin>=6"]
# ///
"""Three-IMU viewer, Firebase publisher, and optional WROOM haptic feedback."""
import argparse
from collections import deque
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import uuid

import firebase_admin
from firebase_admin import credentials, db
import serial

from bridge import parse_sample
from motion_haptics import NeutralFeedback
from viewer import Viewer, multiply
from joystick_calibration import load_calibration

ROOT = Path(__file__).resolve().parents[1]
SENSORS = {"wroom": "Hand / WROOM", "s3_imu1": "Bicep / S3 IMU 1 / 0x4A", "s3_imu2": "Forearm / S3 IMU 2 / 0x4B"}


def open_sensor_port(port):
    """Connect without asserting the ESP32's automatic reset/boot lines."""
    connection = serial.Serial(port=None, baudrate=115200, timeout=.03, write_timeout=.5)
    connection.dtr = False
    connection.rts = False
    connection.port = port
    try:
        connection.open()
        connection.reset_input_buffer()
        return connection
    except Exception:
        connection.close()
        raise


class Hub:
    def __init__(self, wroom_port="COM7", s3_port="COM8", cloud=True):
        self.ports = {"wroom": wroom_port, "s3": s3_port}
        self.session = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.commands = queue.Queue()
        self.states = {key: {"q": (1, 0, 0, 0), "sequence": 0, "seen": 0., "times": deque(maxlen=100), "timestamp_ms": 0} for key in SENSORS}
        self.port_status = {p: "Connecting..." for p in self.ports.values()}
        self.cloud_status = "Connecting to Firebase..." if cloud else "Firebase disabled"
        self.haptic_status = "OFF (checking DRV2605L)"
        self.drv_ready = False
        self.threads = []
        self.cloud = cloud
        self.haptic_source = None  # Optional callback: monotonic time -> (strength, status).
        self.robot_source = None
        self.joystick_calibration = load_calibration()
        self.joystick = {"x": 0, "y": 0, "pressed": False, "sequence": 0,
                         "seen": 0., "timestamp_ms": 0, "uptime_ms": 0, "epoch": 0}

    def start(self):
        jobs = [(self.read_port, (prefix, port)) for prefix, port in self.ports.items()]
        if self.cloud:
            jobs.append((self.publish, ()))
        for target, args in jobs:
            thread = threading.Thread(target=target, args=args, daemon=True)
            self.threads.append(thread)
            thread.start()

    def ingest(self, prefix, line, now=None):
        sensor, q, norm = parse_sample(line, prefix)
        if sensor not in self.states:
            raise ValueError("Unexpected sensor label")
        now = time.monotonic() if now is None else now
        with self.lock:
            state = self.states[sensor]
            state.update(q=tuple(v/norm for v in q), sequence=state["sequence"]+1,
                         seen=now, timestamp_ms=time.time_ns()//1_000_000)
            state["times"].append(now)
        return sensor

    def snapshot(self):
        with self.lock:
            return {key: {**value, "times": tuple(value["times"])} for key, value in self.states.items()}

    def ingest_joystick(self, prefix, line, now=None):
        parts = line.split(",")
        if prefix != "wroom" or len(parts) != 5 or parts[0] != "# JOYSTICK":
            raise ValueError("Expected WROOM joystick packet")
        uptime, x, y, pressed = map(int, parts[1:])
        if not 0 <= uptime <= 0xFFFFFFFF or not 0 <= x <= 4095 or not 0 <= y <= 4095 or pressed not in (0, 1):
            raise ValueError("Invalid joystick packet")
        now = time.monotonic() if now is None else now
        with self.lock:
            previous = self.joystick
            epoch = previous["epoch"] + int(previous["sequence"] > 0 and uptime < previous["uptime_ms"])
            previous.update(x=x, y=y, pressed=bool(pressed), uptime_ms=uptime, epoch=epoch,
                            sequence=previous["sequence"]+1, seen=now,
                            timestamp_ms=time.time_ns()//1_000_000)

    def invalidate_joystick(self):
        with self.lock:
            self.joystick.update(sequence=0, seen=0., epoch=self.joystick["epoch"]+1)

    def joystick_snapshot(self):
        with self.lock:
            return dict(self.joystick)

    def set_port_status(self, port, message):
        with self.lock:
            self.port_status[port] = message

    def read_port(self, prefix, port):
        while not self.stop.is_set():
            try:
                with open_sensor_port(port) as connection:
                    feedback = None
                    external_feedback = False
                    last_strength = 0
                    next_refresh = 0
                    pending_on = None
                    last_sample = time.monotonic()
                    next_status = time.monotonic()+2
                    last_device_status = None
                    self.set_port_status(port, "Connected; waiting for IMU data")
                    if prefix == "wroom":
                        self.invalidate_joystick()
                        self.drv_ready = False
                        connection.write(b"STOP\nSTATUS\n")
                    try:
                        while not self.stop.is_set():
                            now = time.monotonic()
                            if prefix == "wroom":
                                if now >= next_status:
                                    connection.write(b"STATUS\n")
                                    next_status = now+5
                                while not self.commands.empty():
                                    command, q = self.commands.get_nowait()
                                    connection.write(b"STOP\n")
                                    last_strength = 0
                                    feedback = None
                                    external_feedback = False
                                    pending_on = None
                                    if command == "enable" and self.drv_ready and self.haptic_source is not None:
                                        external_feedback = True
                                        self.haptic_status = "ON: motor-load feedback"
                                    elif command == "enable" and self.drv_ready and now-last_sample < .5:
                                        feedback = NeutralFeedback(q)
                                        self.haptic_status = "ON: relative to captured WROOM pose"
                                    else:
                                        self.haptic_status = "OFF" if command == "disable" else "Unavailable: DRV2605L or fresh IMU data missing"
                                if (feedback or external_feedback) and (now-last_sample > .5 or (pending_on is not None and now > pending_on)):
                                    connection.write(b"STOP\n")
                                    feedback = None
                                    external_feedback = False
                                    last_strength = 0
                                    pending_on = None
                                    self.haptic_status = "OFF: IMU data or motor acknowledgment stopped"
                            line = connection.readline().decode("ascii", errors="ignore").strip()
                            if line.startswith("# JOYSTICK"):
                                try:
                                    self.ingest_joystick(prefix, line)
                                except ValueError:
                                    pass
                                continue
                            if prefix == "wroom":
                                if line.startswith("# DEVICE WROOM_IO_V1"):
                                    if line != last_device_status:
                                        print(f"{port}: {line}", flush=True)
                                        last_device_status = line
                                    self.drv_ready = "DRV2605L=1" in line
                                    if not self.drv_ready:
                                        feedback = None
                                        external_feedback = False
                                        connection.write(b"STOP\n")
                                        self.haptic_status = "ESP32 reports DRV2605L not ready; check driver connection, then reset WROOM"
                                    elif feedback is None and not external_feedback:
                                        self.haptic_status = "OFF (DRV2605L detected)"
                                elif line.startswith("# HAPTIC ON"):
                                    pending_on = None
                                elif line.startswith("# ERROR") or line == "# IMU RESET":
                                    if line == "# IMU RESET":
                                        self.invalidate_joystick()
                                    connection.write(b"STOP\n")
                                    feedback = None
                                    external_feedback = False
                                    self.haptic_status = line.removeprefix("# ")
                            try:
                                sensor = self.ingest(prefix, line)
                            except ValueError:
                                if line.startswith("#") and ("missing" in line or "failed" in line):
                                    self.set_port_status(port, line.removeprefix("# "))
                                continue
                            last_sample = time.monotonic()
                            self.set_port_status(port, "Receiving IMU data")
                            if prefix == "wroom" and (feedback or external_feedback):
                                if external_feedback:
                                    strength, self.haptic_status = self.haptic_source(last_sample)
                                else:
                                    q = self.snapshot()[sensor]["q"]
                                    strength, angle = feedback.update(q)
                                    self.haptic_status = f"ON: {angle:.1f} degrees from neutral, strength {strength}/64"
                                if strength == 0 and last_strength:
                                    connection.write(b"STOP\n")
                                    last_strength = 0
                                    pending_on = None
                                elif strength and last_sample >= next_refresh and pending_on is None:
                                    connection.write(f"VIBRATE,300,{strength}\n".encode())
                                    last_strength = strength
                                    pending_on = last_sample+.5
                                    next_refresh = last_sample+(.04 if external_feedback else .1)
                    finally:
                        if prefix == "wroom":
                            self.invalidate_joystick()
                            try:
                                connection.write(b"STOP\n")
                                connection.flush()
                            except serial.SerialException:
                                pass
            except (serial.SerialException, OSError) as error:
                self.set_port_status(port, f"{error}; retrying in 3s")
                if prefix == "wroom":
                    self.drv_ready = False
                    self.haptic_status = "OFF: COM7 disconnected"
            self.stop.wait(3)

    def payload(self, now=None):
        now = time.monotonic() if now is None else now
        batch = {"active_sensors": list(SENSORS), "hub_id": self.session}
        for key, state in self.snapshot().items():
            prefix = "wroom" if key == "wroom" else "s3"
            value = {"sensor_id": key, "port": self.ports[prefix], "hub_id": self.session,
                     "online": bool(state["sequence"] and now-state["seen"] < 2),
                     "sequence": state["sequence"], "timestamp_ms": state["timestamp_ms"]}
            if state["sequence"]:
                value["quaternion"] = dict(zip("wxyz", state["q"]))
            batch[f"sensors/{key}"] = value
        if self.robot_source:
            batch["robot"] = self.robot_source()
        joystick = self.joystick_snapshot()
        batch["controls/wroom_joystick"] = {
            key: joystick[key] for key in ("x", "y", "pressed", "sequence", "timestamp_ms", "epoch")
        }
        batch["controls/wroom_joystick"].update(
            online=bool(joystick["sequence"] and 0 <= now-joystick["seen"] < .3),
            port=self.ports["wroom"], hub_id=self.session)
        controls = batch["controls/wroom_joystick"]
        calibration = self.joystick_calibration
        controls["center"] = {"x": calibration.center_x, "y": calibration.center_y}
        controls["deadzone"] = calibration.deadzone
        controls["normalized"] = calibration.normalized(joystick["x"],joystick["y"]) if controls["online"] else {"x":0.,"y":0.}
        controls["claw_open_fraction"] = calibration.position(joystick["y"],0,1) if controls["online"] else None
        return batch

    def publish(self):
        app = None
        try:
            keys = list((ROOT/"config").glob("*.json"))
            if len(keys) != 1:
                raise ValueError("Expected one service-account JSON in config")
            app = firebase_admin.initialize_app(credentials.Certificate(str(keys[0])), {
                "databaseURL": (ROOT/"config"/"source.txt").read_text().strip(), "httpTimeout": 5,
            }, name=self.session)
            ref = db.reference("live", app=app)
            while not self.stop.is_set():
                try:
                    ref.update(self.payload())
                    self.cloud_status = f"Firebase live | last upload {time.strftime('%H:%M:%S')}"
                except Exception as error:
                    self.cloud_status = f"Firebase upload failed: {type(error).__name__}; retrying"
                    self.stop.wait(2)
                self.stop.wait(.1)
        except Exception as error:
            self.cloud_status = f"Firebase unavailable: {type(error).__name__} (check config/)"
        finally:
            if app:
                # Preserve final readings, but mark this hub's sensors offline.
                try:
                    offline = {f"sensors/{key}/online": False for key in SENSORS}
                    offline["controls/wroom_joystick/online"] = False
                    if self.robot_source:
                        offline["robot/online"] = False
                    ref.update(offline)
                except Exception:
                    pass
                firebase_admin.delete_app(app)

    def close(self):
        self.stop.set()


class Panel(Viewer):
    """Reuse the existing 3D renderer without opening a serial connection."""
    def __init__(self, parent, title):
        self.frame = ttk.Frame(parent, padding=6)
        ttk.Label(self.frame, text=title, font=("Segoe UI", 14, "bold")).pack(pady=6)
        self.status = tk.StringVar(value="Waiting for sensor...")
        ttk.Label(self.frame, textvariable=self.status).pack(pady=4)
        self.canvas = tk.Canvas(self.frame, bg="#101827", highlightthickness=0, width=400, height=430)
        self.canvas.pack(fill="both", expand=True)
        self.q = (1, 0, 0, 0)
        self.reference = (1, 0, 0, 0)
        self.demo = tk.BooleanVar(value=False)


class Dashboard:
    def __init__(self, hub, start=True):
        self.hub = hub
        self.root = tk.Tk()
        self.root.title("Three IMUs | COM7 + COM8 | Firebase")
        self.root.geometry("1380x700")
        self.root.minsize(1000, 560)
        controls = ttk.Frame(self.root, padding=12)
        controls.pack(fill="x")
        ttk.Button(controls, text="Zero all 3D views", command=self.zero).pack(side="left", padx=5)
        haptic_label = "Enable motor-load vibration" if hub.haptic_source else "Enable WROOM vibration at current pose"
        self.haptic_button = ttk.Button(controls, text=haptic_label, command=self.enable_haptics)
        self.haptic_button.pack(side="left", padx=5)
        ttk.Button(controls, text="Stop vibration", command=lambda: hub.commands.put(("disable", None))).pack(side="left", padx=5)
        self.cloud_status = tk.StringVar()
        ttk.Label(controls, textvariable=self.cloud_status).pack(side="right")
        self.status = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status, padding=8).pack(fill="x")
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.panels = {}
        for index, (key, title) in enumerate(SENSORS.items()):
            panel = Panel(body, title)
            panel.frame.grid(row=0, column=index, sticky="nsew")
            body.columnconfigure(index, weight=1, uniform="sensors")
            self.panels[key] = panel
        body.rowconfigure(0, weight=1)
        self.haptic_status = tk.StringVar()
        ttk.Label(self.root, textvariable=self.haptic_status, padding=8).pack(fill="x")
        self.joystick_status = tk.StringVar()
        ttk.Label(self.root, textvariable=self.joystick_status, padding=4).pack(fill="x")
        ttk.Label(self.root, text="Red X / Green Y / Blue Z | Rotation only | Zero changes the display; Firebase receives original quaternions.", padding=8).pack()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if start:
            hub.start()
        self.tick()

    def zero(self):
        for key, state in self.hub.snapshot().items():
            if state["sequence"] and time.monotonic()-state["seen"] < 2:
                w, x, y, z = state["q"]
                self.panels[key].reference = (w, -x, -y, -z)

    def enable_haptics(self):
        state = self.hub.snapshot()["wroom"]
        if state["sequence"] and time.monotonic()-state["seen"] < .5:
            self.hub.commands.put(("enable", state["q"]))

    def tick(self):
        now = time.monotonic()
        states = self.hub.snapshot()
        active = 0
        for key, state in states.items():
            panel = self.panels[key]
            panel.q = state["q"]
            fresh = state["sequence"] and now-state["seen"] < 2
            if fresh:
                active += 1
                stamps = state["times"]
                rate = (len(stamps)-1)/(stamps[-1]-stamps[0]) if len(stamps)>1 and stamps[-1]>stamps[0] else 0
                panel.status.set(f"LIVE  |  {rate:.1f} Hz  |  {state['sequence']} samples")
            else:
                panel.status.set("NO DATA / STALE - check this sensor")
            panel.heading = "LIVE ORIENTATION" if fresh else "WAITING FOR SENSOR"
            panel.draw(multiply(panel.reference, panel.q))
        with self.hub.lock:
            ports = "  |  ".join(f"{port}: {message}" for port, message in self.hub.port_status.items())
        self.status.set(f"{active}/3 IMUs live  |  {ports}")
        self.cloud_status.set(self.hub.cloud_status)
        self.haptic_status.set("WROOM motor: " + self.hub.haptic_status)
        joystick = self.hub.joystick_snapshot()
        fresh_joystick = joystick["sequence"] and 0 <= now-joystick["seen"] < .3
        centered = self.hub.joystick_calibration.normalized(joystick["x"],joystick["y"])
        self.joystick_status.set(
            f"Hand joystick raw X={joystick['x']} Y={joystick['y']} | calibrated X={centered['x']:+.2f} Y={centered['y']:+.2f} | button={'PRESSED' if joystick['pressed'] else 'released'}"
            if fresh_joystick else "Hand joystick: no fresh data (upload updated WROOM firmware)")
        self.haptic_button.config(state="normal" if self.hub.drv_ready and now-states['wroom']['seen']<.5 else "disabled")
        self.root.after(33, self.tick)

    def close(self):
        self.hub.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wroom-port", default="COM7")
    parser.add_argument("--s3-port", default="COM8")
    parser.add_argument("--no-firebase", action="store_true")
    args = parser.parse_args()
    if args.wroom_port.upper() == args.s3_port.upper():
        parser.error("The boards must use different serial ports")
    hub = Hub(args.wroom_port, args.s3_port, cloud=not args.no_firebase)
    app = Dashboard(hub)
    try:
        app.root.mainloop()
    finally:
        hub.close()
        deadline = time.monotonic()+7
        for thread in hub.threads:
            thread.join(timeout=max(0, deadline-time.monotonic()))


if __name__ == "__main__":
    main()
