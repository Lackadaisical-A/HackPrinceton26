# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial>=3.5", "pybullet==3.2.7", "pillow>=10", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "pyzmq>=26"]
# ///
"""Local wearable bench: three IMUs, four coins, and the hand joystick."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wearable_test.configuration import load_model,save_model,config_status
from wearable_test.io import WearableHub, DemoHub, fresh_frame, driver_status
from wearable_test.model import ArmModel, DEFAULTS, ROLES, AXES, stable_average, add, scale, dot, cross
from bno085_reader.joystick_calibration import load_calibration
from robot_control.haptics import HapticFeedback, MOTORS, NAMES as MOTOR_NAMES
from robot_control.joints import JOINTS
from wearable_test.console import CalibrationConsole

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "settings.local.json"
CALIBRATION = HERE / "calibration.local.json"
BG, PANEL, TEXT, MUTED = "#0b1220", "#142033", "#edf3fc", "#a4b5ce"
COLORS = {"upper": "#64b5ff", "forearm": "#4de1bd", "hand": "#ffcb70"}
NAMES = {"upper": "BICEP · S3 / 0x4A", "forearm": "FOREARM · S3 / 0x4B", "hand": "HAND · WROOM"}


class ArmCanvas(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=BG, highlightthickness=0)
        self.azimuth, self.elevation, self.zoom = -65., 24., 1.
        self.projection = None
        self.grid_key = None
        self.anchor = None
        self.bind("<ButtonPress-1>", lambda e: setattr(self, "anchor", (e.x, e.y)))
        self.bind("<B1-Motion>", self.orbit)
        self.bind("<MouseWheel>", self.wheel)

    def orbit(self, event):
        if self.anchor:
            self.azimuth += (event.x-self.anchor[0])*.4
            self.elevation = max(-70, min(80, self.elevation+(event.y-self.anchor[1])*.3))
        self.anchor = event.x, event.y

    def wheel(self, event):
        self.zoom = max(.5, min(2.5, self.zoom*(1.1 if event.delta > 0 else 1/1.1)))

    def prepare_projection(self):
        az, el = map(math.radians, (self.azimuth, self.elevation))
        normal = (math.cos(el)*math.cos(az), math.cos(el)*math.sin(az), math.sin(el))
        right = (-math.sin(az), math.cos(az), 0)
        up = cross(normal, right)
        factor = min(self.winfo_width()/1.08, self.winfo_height()/1.04)*self.zoom
        self.projection = right,up,normal,factor,self.winfo_width()/2,self.winfo_height()/2

    def project(self, p):
        right,up,normal,factor,cx,cy = self.projection
        p = add(p,(-.25,0,.12))
        return (cx+factor*dot(right,p),cy-factor*dot(up,p),dot(normal,p))

    def draw(self, geometry, calibrated, fresh):
        self.prepare_projection()
        self.delete("arm")
        def line(a, b, **kw):
            kw.setdefault("tags","arm")
            self.create_line(*self.project(a)[:2], *self.project(b)[:2], **kw)
        grid_key = self.winfo_width(),self.winfo_height(),self.azimuth,self.elevation,self.zoom
        if grid_key != self.grid_key:
            self.delete("grid")
            for i in range(-6, 11):
                v = i*.1
                line((-.6,v,-.55),(1,v,-.55),fill="#1c2a3d",tags="grid")
                line((v,-.6,-.55),(v,1,-.55),fill="#1c2a3d",tags="grid")
            self.grid_key = grid_key
            self.tag_lower("grid")
        line((0, 0, 0), (0, 0, -.5), fill="#273951", width=32, capstyle=tk.ROUND)
        line((0, -.15, -.02), (0, .15, -.02), fill="#273951", width=24, capstyle=tk.ROUND)
        joints = geometry["joints"]
        objects = []
        for i, role in enumerate(ROLES):
            a, b = joints[i:i+2]
            objects.append((self.project(add(a, scale(add(b, scale(a, -1)), .5)))[2], "bone", (a, b, role)))
        for role, point in geometry["markers"].items():
            objects.append((self.project(point)[2]+.015, "sensor", (role, point)))
        for _, kind, data in sorted(objects):
            if kind == "bone":
                a, b, role = data
                color = COLORS[role] if calibrated and fresh else "#596b83"
                line(a, b, fill="#25354b", width=30 if role == "upper" else 23, capstyle=tk.ROUND)
                line(a, b, fill=color, width=5, capstyle=tk.ROUND)
                if role == "hand":
                    side = geometry["bases"][role][1]
                    palm = [add(p, scale(side, s*.045)) for p, s in ((a, -1), (b, -1), (b, 1), (a, 1))]
                    self.create_polygon(*[c for p in palm for c in self.project(p)[:2]], fill="#493e2d", outline=color, width=2,tags="arm")
            else:
                role, point = data
                axes = geometry["boards"][role]
                corners = [add(add(point, scale(axes[0], x*.026)), scale(axes[1], y*.019)) for x, y in ((-1,-1),(1,-1),(1,1),(-1,1))]
                self.create_polygon(*[c for p in corners for c in self.project(p)[:2]], fill="#203846", outline=COLORS[role], width=2,tags="arm")
                for axis, color in zip(axes, ("#ff767d", "#75e6a1", "#70a8ff")):
                    line(point, add(point, scale(axis, .055)), fill=color, width=2)
        for i, point in enumerate(joints[:3]):
            x, y, _ = self.project(point)
            self.create_oval(x-7, y-7, x+7, y+7, fill=TEXT, outline=BG, width=2,tags="arm")
            self.create_text(x+12, y+18, text=("Shoulder anchor", "Elbow", "Wrist")[i], fill=MUTED, anchor="w", font=("Segoe UI", 9),tags="arm")
        offsets = {"upper": (8,-48), "forearm": (8,42), "hand": (8,-44)}
        for role, p in geometry["markers"].items():
            x, y, _ = self.project(p)
            dx, dy = offsets[role]
            self.create_line(x, y, x+dx, y+dy, fill=COLORS[role], dash=(3,3),tags="arm")
            self.create_text(x+dx, y+dy, text=NAMES[role], fill=COLORS[role], anchor="w", font=("Segoe UI", 10, "bold"),tags="arm")
        caption = "LIVE ORIENTATION / ESTIMATED POSITION" if calibrated else "REFERENCE POSE / CALIBRATION NEEDED"
        if calibrated and not fresh:
            caption = "READINGS STALE / LAST POSE HELD"
        self.create_text(20, 22, text=caption, anchor="w", fill=MUTED, font=("Segoe UI", 10, "bold"),tags="arm")
        self.create_text(20, 46, text=f"Elbow bend {geometry['elbow_deg']:.0f}°   ·   Wrist bend {geometry['wrist_deg']:.0f}°", anchor="w", fill=TEXT, font=("Segoe UI", 11),tags="arm")
        self.create_text(20, self.winfo_height()-25, text="Drag to orbit  ·  Scroll to zoom  ·  Board axes: X red / Y green / Z blue", anchor="w", fill=MUTED, font=("Segoe UI", 9),tags="arm")


class App:
    console = None
    settings_dialog = None  # The open Tk dimensions dialog; web edits wait until it closes.

    def __init__(self, root, hub, physical=None, vision_endpoint='tcp://127.0.0.1:5555', commands_endpoint='tcp://127.0.0.1:5557',
                 haptics_endpoint='tcp://127.0.0.1:5561', calibration_endpoint='tcp://127.0.0.1:5562'):
        self.root, self.hub = root, hub
        self.physical,self.physical_panel = physical,None
        self.feedback = HapticFeedback(hub,physical,vision_endpoint,commands_endpoint,haptics_endpoint) if physical is not None else None
        if physical is not None: physical.haptics = self.feedback
        self.calibration_revision = 0
        self.root.title("Wearable Lab — 3 IMUs · 4 haptics · joystick"+(" [DEMO]" if hub.demo else ""))
        self.root.configure(bg=BG)
        self.root.geometry(f"{min(1400, root.winfo_screenwidth()-60)}x{min(940, root.winfo_screenheight()-90)}")
        self.root.minsize(1060, 760)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 6))
        style.configure("TScale", background=PANEL)
        self.message = tk.StringVar(value="Start with pose A: arm straight forward, shoulder height, palm down.")
        self.model,self.saved_config = load_model(SETTINGS,CALIBRATION,hub.demo)
        self.saved_config_label = tk.StringVar(value=self.saved_config['detail'])
        self.pose_a = self.pose_b = self.capture = None
        self.recovery_reason = ""
        self.tick_error = None
        if self.saved_config['state'] in ('loaded','error'):
            self.message.set(self.saved_config['detail'])
            if self.saved_config['state']=='loaded': self.recovery_reason = self.saved_config['detail']
        self.joystick_calibration = load_calibration()
        # Per-joint direction flips, shared by the URDF preview window and the web console.
        self.reverse = dict.fromkeys(JOINTS, False)
        # Manual coin-test intensity per (board, motor), shared by the Tk sliders and the web console.
        self.motor_percent = {key: 40. for key in MOTORS}
        self.robot_preview = None
        self.epochs = None
        self.geometry = self.model.geometry()
        self.sensor_labels, self.board_labels, self.motor_widgets = {}, {}, {}
        self.closed = False
        self.next_status_ui = 0.
        self.build()
        if hub.demo:
            self.pose_a, self.pose_b = hub.frame("A"), hub.frame("B")
            self.model = ArmModel()
            self.model.calibrate(self.pose_a, self.pose_b)
            self.message.set("DEMO: synthetic animation and controls. No serial ports or motors are used.")
        self.hub.start()
        if self.physical is not None:
            self.physical.bind_hub(self.hub)
            self.physical.start()
            self.feedback.start()
            self.open_physical_controls()
        # Also without a robot: the browser can calibrate and test coins on the sensor bench / demo.
        try:
            self.console = CalibrationConsole(self, commands_endpoint, calibration_endpoint)
        except Exception:
            logging.getLogger('wearable').exception('Web calibration unavailable')
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda e: self.stop_all())
        self.tick()

    def build(self):
        header = ttk.Frame(self.root, padding=(20,14))
        header.pack(fill="x")
        ttk.Label(header, text="WEARABLE LAB"+("  /  DEMO" if self.hub.demo else ""), font=("Segoe UI",20,"bold")).pack(side="left")
        ttk.Button(header, text="STOP / HOLD ALL  [Esc]" if self.physical else "STOP ALL MOTORS  [Esc]", command=self.stop_all).pack(side="right")
        ttk.Button(header, text="Dimensions / sensor axes", command=self.settings).pack(side="right", padx=10)
        self.recenter_button = ttk.Button(header,text="Recenter arm · pose A",command=lambda: self.begin_capture("R"),state="disabled")
        self.recenter_button.pack(side="right")
        if self.physical is not None:
            ttk.Button(header,text="Robot controls",command=self.open_physical_controls).pack(side="right",padx=8)
        middle = ttk.Frame(self.root)
        middle.pack(fill="both", expand=True, padx=14)
        self.canvas = ArmCanvas(middle)
        self.canvas.pack(side="left", fill="both", expand=True)
        sidebar = ttk.Frame(middle, style="Panel.TFrame", padding=14, width=370)
        sidebar.pack(side="right", fill="y", padx=(12,0))
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="CALIBRATE YOUR ARM", style="Panel.TLabel", font=("Segoe UI",12,"bold")).pack(anchor="w")
        ttk.Label(sidebar, text="A  Extend straight forward at shoulder height.\n     Keep your palm down and wrist straight.\nB  Lower the straight arm toward your side.\n     Do not twist, bend the elbow, or move torso.", style="Muted.TLabel", justify="left").pack(anchor="w", pady=8)
        buttons = ttk.Frame(sidebar, style="Panel.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="1 · Capture A", command=lambda: self.begin_capture("A")).pack(side="left", expand=True, fill="x", padx=(0,4))
        ttk.Button(buttons, text="2 · Capture B", command=lambda: self.begin_capture("B")).pack(side="left", expand=True, fill="x")
        ttk.Label(sidebar, text="Each capture: 3s to get ready, then hold still 2s.\nAuto axes learn the mounting from both poses.", style="Muted.TLabel").pack(anchor="w", pady=(5,7))
        ttk.Label(sidebar,text="FULL JOINT RANGE (after A/B or recenter)\nC  Bend your elbow to your comfortable maximum.\nD  Raise your straight arm to your highest reach.",style="Muted.TLabel").pack(anchor='w',pady=(4,4))
        ranges = ttk.Frame(sidebar,style='Panel.TFrame')
        ranges.pack(fill='x',pady=(0,6))
        ttk.Button(ranges,text='3 · Bent elbow',command=lambda:self.begin_capture('C')).pack(side='left',expand=True,fill='x',padx=(0,4))
        ttk.Button(ranges,text='4 · Arm raised',command=lambda:self.begin_capture('D')).pack(side='left',expand=True,fill='x')
        ttk.Label(sidebar,text='PAN: reach to each comfortable side; torso still.\nPose A stays centered. Capture both sides.',style='Muted.TLabel').pack(anchor='w',pady=(0,4))
        pan_ranges = ttk.Frame(sidebar,style='Panel.TFrame')
        pan_ranges.pack(fill='x',pady=(0,6))
        ttk.Button(pan_ranges,text='5 · Pan left',command=lambda:self.begin_capture('E')).pack(side='left',expand=True,fill='x',padx=(0,4))
        ttk.Button(pan_ranges,text='6 · Pan right',command=lambda:self.begin_capture('F')).pack(side='left',expand=True,fill='x')
        self.robot_button = ttk.Button(sidebar,text="Enable SO-101 URDF preview",command=self.open_robot_preview,state="disabled")
        self.robot_button.pack(fill="x",pady=(0,4))
        ttk.Label(sidebar,text="Recenter keeps learned mounting after a reset.",style="Muted.TLabel").pack(anchor="w",pady=(0,6))
        ttk.Label(sidebar,textvariable=self.saved_config_label,style='Muted.TLabel',wraplength=340).pack(anchor='w',pady=(0,4))
        for role, sensor in ROLES.items():
            label = ttk.Label(sidebar, text=NAMES[role]+"\nWaiting for IMU…", style="Panel.TLabel", foreground=COLORS[role])
            label.pack(anchor="w", pady=(1,4))
            self.sensor_labels[sensor] = label
        for name in ("wroom", "s3"):
            label = ttk.Label(sidebar, text="Waiting for board", style="Muted.TLabel", wraplength=340)
            label.pack(anchor="w", pady=3)
            self.board_labels[name] = label
        joystick_panel = ttk.Frame(self.canvas, style="Panel.TFrame", padding=10)
        joystick_panel.place(relx=0, rely=1, anchor="sw", x=16, y=-48)
        ttk.Label(joystick_panel, text="HAND JOYSTICK · D2 / D4 / D5", style="Panel.TLabel", font=("Segoe UI",10,"bold")).pack(anchor="w", pady=(0,6))
        joystick_row = ttk.Frame(joystick_panel, style="Panel.TFrame")
        joystick_row.pack(fill="x")
        self.joy_canvas = tk.Canvas(joystick_row, width=130, height=130, bg=BG, highlightthickness=0)
        self.joy_canvas.pack(side="left")
        self.joy_label = ttk.Label(joystick_row, text="Waiting for joystick", style="Panel.TLabel", justify="left")
        self.joy_label.pack(side="left", padx=12)
        cal = self.joystick_calibration
        ttk.Label(joystick_panel, text=f"Center X={cal.center_x:g} / Y={cal.center_y:g}\n{cal.deadzone:.0%} dead zone · calibrated output −1…+1", style="Muted.TLabel").pack(anchor="w", pady=(6,0))
        if self.feedback is not None:
            feedback_row = ttk.Frame(self.root,padding=(14,5))
            feedback_row.pack(fill='x')
            self.feedback_button = ttk.Button(feedback_row,text='Enable guidance + resistance',command=self.toggle_feedback)
            self.feedback_button.pack(side='left',padx=(0,12))
            self.feedback_label = ttk.Label(feedback_row,text='Forearm = UP. Palm = DOWN. Taps guide; steady vibration means resistance.')
            self.feedback_label.pack(side='left')
        motors = ttk.Frame(self.root, padding=(14,10))
        motors.pack(fill="x")
        for index, (board, motor) in enumerate(MOTORS):
            frame = ttk.Frame(motors, style="Panel.TFrame", padding=10)
            frame.grid(row=0, column=index, sticky="nsew", padx=(0,8) if index < 3 else 0)
            motors.columnconfigure(index, weight=1, uniform="motors")
            ttk.Label(frame, text=MOTOR_NAMES[index], style="Panel.TLabel", font=("Segoe UI",10,"bold")).pack(anchor="w")
            value = tk.DoubleVar(value=self.motor_percent[(board,motor)])
            label = ttk.Label(frame, text=f"{value.get():.0f}% · idle", style="Muted.TLabel")
            label.pack(anchor="w", pady=(4,0))
            slider = ttk.Scale(frame, from_=0, to=100, variable=value,
                               command=lambda v,b=board,m=motor: self.set_motor_percent(b,m,float(v)))
            slider.pack(fill="x", pady=5)
            row = ttk.Frame(frame, style="Panel.TFrame")
            row.pack(fill="x")
            test = ttk.Button(row, text="Test 1s", command=lambda b=board,m=motor: self.run_motor(b,m,1))
            run = ttk.Button(row, text="Run 10s", command=lambda b=board,m=motor: self.run_motor(b,m,10))
            stop = ttk.Button(row, text="Stop", command=lambda b=board,m=motor: self.hub.stop(b,m))
            for button in (test, run, stop):
                button.pack(side="left", expand=True, fill="x")
            status = ttk.Label(frame, text="Waiting for device status", style="Muted.TLabel", wraplength=270)
            status.pack(anchor="w", pady=(5,0))
            self.motor_widgets[(board,motor)] = (value,label,test,run,status)
        ttk.Label(self.root, textvariable=self.message, wraplength=1320, padding=(20,3), foreground="#ffcb70").pack(fill="x")
        ttk.Label(self.root, text="Positions are estimated from orientation + arm lengths. Shoulder stays fixed.  •  Motor ACKs confirm commands; feel each coin to confirm vibration.", font=("Segoe UI",9), foreground=MUTED, padding=(20,8)).pack(fill="x")

    def set_motor_percent(self, board, motor, percent):
        """Next manual test level; also adjusts a test that is already running."""
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("Intensity must be 0-100%")
        self.motor_percent[(board,motor)] = percent
        self.hub.intensity(board, motor, percent)

    def run_motor(self, board, motor, seconds, percent=None):
        """Start a manual coin test. Returns an error message, or None once started."""
        if self.capture:
            self.message.set("Finish calibration before testing motors.")
            return "Finish calibration before testing motors."
        try:
            if percent is not None: self.set_motor_percent(board, motor, percent)
            if self.feedback is not None: self.feedback.stop('manual motor test')
            self.hub.motor(board, motor, self.motor_percent[(board,motor)], seconds)
            self.message.set(f"{board.upper()} motor {motor}: test started. Adjust the slider while it runs; Esc stops all.")
        except ValueError as error:
            self.message.set(str(error))
            return str(error)
        return None

    def toggle_feedback(self):
        if self.feedback.snapshot()['enabled']: self.feedback.stop('operator switch')
        elif not self.capture: self.feedback.enable()

    def open_robot_preview(self):
        if not self.model.calibrated or self.capture:
            self.message.set("Complete arm calibration before enabling the SO-101 preview.")
            return
        try:
            fresh_frame(self.hub.snapshot(),time.monotonic())
            if self.robot_preview is None or self.robot_preview.closed:
                from wearable_test.robot_preview import RobotPreview
                self.robot_preview = RobotPreview(self.root,self.joystick_calibration,self.stop_all,
                                                  lambda: self.begin_capture("R"),self.physical.pause if self.physical else None,
                                                  reverse=self.reverse,on_reverse=self.set_reverse)
            else:
                self.robot_preview.enable()
            self.message.set("SO-101 preview enabled. Calibrated IMUs drive the arm; joystick Y sets claw position. Simulation only.")
        except Exception as error:
            self.message.set(f"Could not open SO-101 preview: {error}")

    def stop_all(self):
        if self.console is not None: self.console.invalidate()
        self.capture = None
        if self.hub.demo: self.hub.pose = None
        if self.feedback is not None: self.feedback.stop('operator Stop / Esc')
        if self.physical is not None: self.physical.pause("operator Stop / Esc")
        self.hub.stop()
        self.message.set("Physical arm/claw paused; wearable motors stopped." if self.physical else "All motor outputs stopped.")

    def set_reverse(self, joint, reverse):
        """Flip one joint's mapping direction (URDF preview checkbox or web console)."""
        if joint not in self.reverse: raise ValueError("Unknown joint")
        if self.reverse[joint] == bool(reverse): return
        self.reverse[joint] = bool(reverse)
        if self.console is not None: self.console.invalidate()
        # The physical input epoch includes every direction, so active following pauses itself.
        self.message.set(f"{joint.replace('_',' ').title()} direction {'reversed' if reverse else 'normal'}. "
                         "Following paused; enable the arm again when ready.")
        logging.getLogger("wearable").info("Joint direction %s reverse=%s",joint,bool(reverse))

    def open_physical_controls(self):
        if self.physical is None: return
        if self.physical_panel is None or self.physical_panel.closed:
            from robot_control.wearable_ui import PhysicalPanel
            self.physical_panel = PhysicalPanel(self.root,self.physical,self.stop_all)
        else: self.physical_panel.window.lift()

    def begin_capture(self, pose, lift=True):
        if self.capture:
            return
        if pose in ('C','D','E','F') and not self.model.calibrated:
            self.message.set('Calibrate A/B or recenter before learning elbow and shoulder range.')
            return
        if pose == "B" and self.pose_a is None:
            self.message.set("Capture pose A first, then lower your straight arm for pose B.")
            return
        if pose == "R" and self.model.mounts is None:
            self.message.set("Capture A and B once to learn mounting before using Recenter arm.")
            return
        try:
            snapshot = self.hub.snapshot()
            fresh_frame(snapshot, time.monotonic())
        except ValueError as error:
            self.message.set(str(error))
            return
        if self.console is not None: self.console.invalidate()
        if self.feedback is not None: self.feedback.stop('calibration/recenter started')
        self.hub.stop()
        if self.physical is not None: self.physical.pause("calibration/recenter started")
        if pose == "A":
            self.pose_a = self.pose_b = None
            self.model.reference = self.model.mounts = None
            self.model.motion_ranges = {}
        elif pose == "R":
            self.model.invalidate_reference()
        if self.hub.demo:
            self.hub.pose = "A" if pose == "R" else pose
        if lift: self.root.lift()
        self.capture = {"pose": pose, "start": time.monotonic(), "samples": {s: [] for s in ROLES.values()},
                        "last": {}, "epochs": tuple(b["epoch"] for b in snapshot.values())}

    def capture_tick(self, snapshot, now):
        capture = self.capture
        if capture is None:
            return
        try:
            if capture["epochs"] != tuple(b["epoch"] for b in snapshot.values()):
                raise ValueError("Board/IMU restarted during capture; start again with pose A")
            frame, sequences = fresh_frame(snapshot, now)
            elapsed = now-capture["start"]
            pose = capture["pose"]
            label = {'R':'RECENTER: straight forward, palm down','E':'PAN LEFT: comfortable left reach, torso still',
                     'F':'PAN RIGHT: comfortable right reach, torso still'}.get(pose,f'POSE {pose}')
            if elapsed < 3:
                self.message.set(f"{label}: get into position… {math.ceil(3-elapsed)}")
                return
            self.message.set(f"{label}: HOLD STILL… {max(0, 5-elapsed):.1f}s")
            for sensor, q in frame.items():
                if sequences[sensor] != capture["last"].get(sensor):
                    capture["samples"][sensor].append(q)
                    capture["last"][sensor] = sequences[sensor]
            if elapsed < 5:
                return
            average = {sensor: stable_average(samples) for sensor, samples in capture["samples"].items()}
            self.capture = None
            if pose == "A":
                self.pose_a = average
                if all(axis != "Auto" for axis in self.model.settings["axes"].values()):
                    self.model.calibrate(self.pose_a)
                    self.calibration_ready("Calibrated with manual axes. Move your arm to check the model.")
                else:
                    self.message.set("Pose A saved. Now lower your straight arm toward the floor and capture B.")
            elif pose == "R":
                self.model.recenter(average)
                self.pose_a,self.pose_b = average,None
                self.calibration_ready("Recentered. Mounting and learned elbow, shoulder and pan ranges were kept. Enable robot control when ready.")
            elif pose in ('C','D','E','F'):
                self.calibration_ready(self.model.capture_motion_range(pose,average))
            else:
                self.pose_b = average
                self.model.calibrate(self.pose_a, self.pose_b)
                self.calibration_ready("Calibrated. Recenter arm after a reset; repeat A/B only if a sensor strap moves.")
        except ValueError as error:
            self.capture = None
            if self.hub.demo: self.hub.pose = None
            self.message.set(f"Calibration: {error}")

    def calibration_ready(self, message):
        if self.console is not None: self.console.invalidate()
        self.calibration_revision += 1
        if self.physical is not None: self.physical.pause("arm reference changed; enable again from this pose")
        self.recovery_reason = ""
        if self.hub.demo:
            self.hub.pose = None
        else:
            try:
                self.saved_config = save_model(self.model,CALIBRATION)
            except (OSError,ValueError) as error:
                self.saved_config = config_status(CALIBRATION,'error',f'Configuration could not be saved: {error}. Previous saved file was kept.')
                message += ' '+self.saved_config['detail']
            if hasattr(self,'saved_config_label'): self.saved_config_label.set(self.saved_config['detail'])
        if self.robot_preview is not None and not self.robot_preview.closed:
            self.robot_preview.calibration_changed()
        logging.getLogger("wearable").info(message)
        self.message.set(message)

    def settings(self):
        if self.capture:
            self.message.set("Finish the current capture before changing dimensions or axes.")
            return
        if self.console is not None: self.console.invalidate()
        if self.physical is not None: self.physical.pause("editing arm calibration settings")
        if self.settings_dialog is not None:
            self.settings_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.settings_dialog = dialog
        dialog.bind("<Destroy>", lambda event: setattr(self, "settings_dialog", None) if event.widget is dialog else None)
        dialog.title("Arm dimensions and IMU mounting")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        fields = {}
        specs = (("upper_cm","Shoulder → elbow (cm)"), ("forearm_cm","Elbow → wrist (cm)"), ("hand_cm","Wrist → fingertips (cm)"),
                 ("bicep_from_elbow_pct","Bicep IMU: % from elbow toward shoulder"), ("forearm_from_wrist_pct","Forearm IMU: % from wrist toward elbow"),
                 ("hand_from_wrist_pct","Hand IMU: % from wrist toward fingertips"))
        for row, (key, label) in enumerate(specs):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5)
            fields[key] = tk.StringVar(value=f"{self.model.settings[key]:.3f}")
            ttk.Entry(body, textvariable=fields[key], width=12).grid(row=row, column=1, padx=12)
        axes = {}
        for row, role in enumerate(ROLES, start=len(specs)):
            ttk.Label(body, text=f"{role.title()}: board axis toward fingers in pose A").grid(row=row, column=0, sticky="w", pady=5)
            axes[role] = tk.StringVar(value=self.model.settings["axes"][role])
            ttk.Combobox(body, values=("Auto", *AXES), textvariable=axes[role], width=10, state="readonly").grid(row=row,column=1,padx=12)
        row = len(specs)+len(ROLES)
        ttk.Label(body, text="Auto learns arbitrary mounting from A + B. With all axes selected manually,\ncapture A alone. Sensor positions run along the limb centerline.\nSaving clears calibration; local settings are saved only on this computer.", foreground=MUTED).grid(row=row,column=0,columnspan=2, pady=14, sticky="w")
        def save():
            try:
                settings = {k: float(v.get()) for k,v in fields.items()}
                settings["axes"] = {k:v.get() for k,v in axes.items()}
                self.apply_arm_settings(settings)
            except (ValueError, OSError) as error:
                messagebox.showerror("Check settings", str(error), parent=dialog)
                return
            dialog.destroy()
        ttk.Button(body, text="Save and recalibrate", command=save).grid(row=row+1,column=0,columnspan=2,sticky="ew")

    def apply_arm_settings(self, settings):
        """Validate, persist and adopt new dimensions/axes; this clears the current calibration."""
        model = ArmModel(settings)
        SETTINGS.write_text(json.dumps(model.settings, indent=2)+"\n")
        self.stop_all()
        self.model = model
        self.pose_a = self.pose_b = None
        self.geometry = self.model.geometry()
        self.recovery_reason = "Settings changed; capture A/B."
        self.saved_config = config_status(CALIBRATION,'none','Dimensions/axes changed. Capture A/B to save matching calibration; the previous calibration file was kept.')
        if hasattr(self,'saved_config_label'): self.saved_config_label.set(self.saved_config['detail'])
        if self.robot_preview is not None and not self.robot_preview.closed:
            self.robot_preview.calibration_changed()
        self.message.set("Settings saved. Capture pose A to start calibration.")
        logging.getLogger("wearable").info("Arm settings changed: %s",model.settings)

    def tick(self):
        if self.closed:
            return
        try:
            self.tick_body()
            self.tick_error = None
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if detail != self.tick_error:
                logging.getLogger("wearable").exception("Pose update failed; will retry")
            self.tick_error = detail
            if self.physical is not None: self.physical.pause("wearable pose update error")
            self.message.set(f"Pose update error (retrying): {detail}")
            if self.robot_preview is not None and not self.robot_preview.closed:
                self.robot_preview.update(self.geometry,None,False,False,time.monotonic(),self.message.get())
        finally:
            if not self.closed: self.root.after(20,self.tick)

    def tick_body(self):
        if self.feedback is not None: self.feedback.heartbeat()
        snapshot = self.hub.snapshot()
        now = time.monotonic()
        epochs = tuple(b["epoch"] for b in snapshot.values())
        if self.epochs is not None and self.epochs != epochs:
            if self.console is not None: self.console.invalidate()
            if self.physical is not None: self.physical.pause("board/IMU restarted or reconnected")
            changed = [f"{board['port']}: {board.get('reset_reason') or board['message']}"
                       for index,board in enumerate(snapshot.values()) if epochs[index] != self.epochs[index]]
            self.recovery_reason = "; ".join(changed)
            self.model.invalidate_reference()
            self.pose_a = self.pose_b = self.capture = None
            action = "Extend straight forward, palm down, then Recenter arm." if self.model.mounts is not None else "Capture A and B."
            self.message.set(self.recovery_reason+" · "+action)
            logging.getLogger("wearable").info("Arm reference held: %s",self.message.get())
        self.epochs = epochs
        if self.console is not None: self.console.poll()
        self.capture_tick(snapshot, now)
        stale_reason = ""
        try:
            frame, _ = fresh_frame(snapshot, now)
            if self.model.calibrated: self.geometry = self.model.geometry(frame)
            fresh = True
        except ValueError as error:
            fresh = False
            stale_reason = str(error)
            if self.model.mounts is None:
                self.geometry = self.model.geometry()
        if self.physical is not None:
            reverse = self.reverse
            self.physical.submit_reference(self.model,snapshot,self.model.calibrated and not self.capture,
                                             stale_reason or self.recovery_reason or "calibrate/recenter the arm",
                                             self.calibration_revision,reverse,now)
        self.canvas.draw(self.geometry,self.model.calibrated,fresh)
        self.robot_button.configure(state="normal" if self.model.calibrated and fresh and not self.capture else "disabled")
        self.recenter_button.configure(state="normal" if self.model.mounts is not None and fresh and not self.capture else "disabled")
        if self.robot_preview is not None and not self.robot_preview.closed:
            reason = stale_reason
            if self.capture:
                reason = self.message.get()
            elif not self.model.calibrated:
                action = "Recenter arm in pose A" if self.model.mounts is not None else "Capture A and B"
                reason = (self.recovery_reason+"; " if self.recovery_reason else "")+action
            self.robot_preview.update(self.geometry,snapshot["wroom"]["joystick"],self.model.calibrated and not self.capture,fresh,now,reason)
        if self.console is not None: self.console.publish(snapshot, now)
        if now < self.next_status_ui:
            return
        self.next_status_ui = now+.1
        if self.feedback is not None:
            feedback = self.feedback.snapshot()
            self.feedback_button.configure(text='Pause vibration feedback' if feedback['enabled'] else 'Enable guidance + resistance',
                                           state='disabled' if self.capture else 'normal')
            self.feedback_label.configure(text=feedback['detail'])
        for name, board in snapshot.items():
            fields = board["fields"]
            status = "connected" if board["connected"] else "OFFLINE"
            driver = driver_status(board, now)
            self.board_labels[name].configure(text=f"{board['port']} · {status} · DRV2605L {driver}")
            for sensor, sample in board["samples"].items():
                role = next(role for role, key in ROLES.items() if key == sensor)
                age = now-sample["seen"] if sample["q"] is not None else math.inf
                times = sample["times"]
                hz = (len(times)-1)/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] and age < .4 else 0
                detail = f"LIVE  {hz:.0f} Hz · {age*1000:.0f} ms ago" if age < .4 else "STALE / no fresh orientation"
                self.sensor_labels[sensor].configure(text=NAMES[role]+"\n"+detail)
            for motor in (1,2):
                value, label, test, run, status_label = self.motor_widgets[(name,motor)]
                if abs(value.get()-self.motor_percent[(name,motor)]) > .5: value.set(self.motor_percent[(name,motor)])  # Web edit.
                state = board["motors"][motor]
                available = board["connected"] and now-board["status_at"] < 6 and fields.get("DRV2605L" if motor==1 else "PWM2") == "1"
                for button in (test, run):
                    button.configure(state="normal" if available and not self.capture else "disabled")
                remaining = max(0, state["until"]-now)
                percent = state['percent'] if state.get('feedback') else value.get()
                label.configure(text=f"{percent:.0f}% · "+(f"FEEDBACK · {state['acks']} ACKs" if state.get('feedback') else f"RUNNING {remaining:.1f}s · {state['acks']} ACKs" if state["percent"] else "idle"))
                if not available:
                    detail = driver if motor==1 else "PWM unavailable / no recent status"
                elif "stopped:" in board["message"] or board["message"].startswith("ERROR"):
                    detail = board["message"]
                else:
                    detail = "Driver detected at 0x5A" if motor==1 else "PWM ready · motor presence not detectable"
                status_label.configure(text=detail)
        joystick = snapshot["wroom"]["joystick"]
        jc = self.joy_canvas
        jc.delete("all")
        jc.create_rectangle(10,10,120,120,outline="#405574")
        jc.create_line(65,10,65,120, fill="#405574")
        jc.create_line(10,65,120,65, fill="#405574")
        if joystick and now-joystick["seen"] < .4:
            axes = self.joystick_calibration.normalized(joystick["x"],joystick["y"])
            x, y = 65+55*axes["x"], 65-55*axes["y"]
            jc.create_oval(x-7,y-7,x+7,y+7,fill="#ffcb70" if joystick["pressed"] else "#4de1bd",outline="")
            self.joy_label.configure(text=f"X {joystick['x']:4d} → {axes['x']:+.2f}\nY {joystick['y']:4d} → {axes['y']:+.2f}\n{'NEUTRAL' if not any(axes.values()) else 'DEFLECTED'}\nSW {'PRESSED' if joystick['pressed'] else 'released'}")
        else:
            self.joy_label.configure(text="STALE / no joystick\nCheck WROOM\nfirmware and D2/4/5")
        # Expose the most recent connection error without drowning out capture instructions.
        offline = [b["message"] for b in snapshot.values() if not b["connected"]]
        if offline and not self.capture:
            self.message.set(offline[0])

    def close(self):
        self.closed = True
        if self.console is not None: self.console.close()
        self.hub.stop()
        if self.physical is not None: self.physical.close()
        if self.physical_panel is not None: self.physical_panel.close()
        if self.robot_preview is not None:
            self.robot_preview.close()
        self.hub.close()
        self.root.destroy()


def main():
    logger = logging.getLogger("wearable")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(HERE/"runtime.log",maxBytes=500_000,backupCount=2,encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wroom", default="COM7")
    parser.add_argument("--s3", default="COM8")
    parser.add_argument("--robot-port",help="Optional physical SO-101 port; connects read-only until manual enable")
    parser.add_argument("--telemetry", action="store_true", help="Publish measured IMU-controlled robot feedback to the web console")
    parser.add_argument("--telemetry-endpoint", default="tcp://127.0.0.1:5558")
    parser.add_argument("--vision-endpoint", default="tcp://127.0.0.1:5555")
    parser.add_argument("--commands-endpoint", default="tcp://127.0.0.1:5557")
    parser.add_argument("--haptics-endpoint", default="tcp://127.0.0.1:5561")
    parser.add_argument("--calibration-endpoint", default="tcp://127.0.0.1:5562")
    parser.add_argument("--assist-endpoint", default="tcp://127.0.0.1:5563",
                        help="where the assist process publishes Gemini's smart-replay actions")
    parser.add_argument("--demo", action="store_true", help="Synthetic inputs, no serial ports or physical motor writes")
    parser.add_argument("--check", action="store_true", help="Read telemetry for 6 seconds, print health, release ports")
    args = parser.parse_args()
    if args.telemetry and not args.robot_port:
        parser.error("--telemetry requires --robot-port for measured feedback")
    if args.robot_port and (args.demo or args.check):
        parser.error("Physical robot mode cannot use --demo or --check. Use sensor.ps1 robot-check for read-only checks.")
    logger.info("Starting Wearable Lab (%s)","demo" if args.demo else f"live {args.wroom}/{args.s3}")
    hub = DemoHub() if args.demo else WearableHub(args.wroom, args.s3)
    if args.check:
        hub.start()
        try:
            deadline = time.monotonic()+6
            while time.monotonic() < deadline:
                if hub.demo: hub.snapshot()
                time.sleep(.04)
            snap = hub.snapshot()
            now = time.monotonic()
            for name, board in snap.items():
                print(name, board["port"], "connected=", board["connected"], "status=", board["fields"])
                for sensor, sample in board["samples"].items():
                    print(" ", sensor, "samples=", sample["sequence"], "fresh=", sample["q"] is not None and now-sample["seen"] < .4)
                if board["joystick"]:
                    print("  joystick:", {k:v for k,v in board["joystick"].items() if k != "seen"})
                print(" ", board["message"])
        finally:
            hub.close()
        return
    root = tk.Tk()
    physical = None
    if args.robot_port:
        from robot_control.arm import choose_port
        from robot_control.wearable import WearableRobot
        try:
            physical = WearableRobot(choose_port(args.robot_port,(args.wroom,args.s3)),load_calibration(),
                                     telemetry_endpoint=args.telemetry_endpoint if args.telemetry else None)
        except (ValueError,OSError) as error:
            root.destroy()
            parser.error(str(error))
        from robot_control.agent_link import AgentInbox
        physical.agent_inbox = AgentInbox(args.assist_endpoint)
        physical.agent_inbox.start()
    App(root, hub, physical,args.vision_endpoint,args.commands_endpoint,args.haptics_endpoint,args.calibration_endpoint)
    try:
        root.mainloop()
    finally:
        if physical is not None: physical.close()
        hub.close()


if __name__ == "__main__":
    main()
