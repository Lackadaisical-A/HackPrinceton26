"""Calibrated limb orientations -> bounded SO-101 URDF pose. No robot USB access."""
import math
import hashlib
import json
import logging
from pathlib import Path
import time

from bno085_reader.joystick_calibration import load_calibration
from wearable_test.retarget import Retargeter
from wearable_test.render_transport import RenderClient

ROOT = Path(__file__).resolve().parents[1]
URDF = ROOT/"so101_sim/model/so101_new_calib.urdf"
DISPLAY_MODEL = ROOT/"wearable_test/model_preview"
JOINT_LABELS = dict(shoulder_pan="Base turn",shoulder_lift="Shoulder lift",elbow_flex="Elbow bend",
                    wrist_flex="Wrist bend",wrist_roll="Wrist roll",gripper="Claw")


def render_model():
    """Fall back to the source if display assets are absent or its joints changed."""
    try:
        manifest = json.loads((DISPLAY_MODEL/"manifest.json").read_text())
        path = DISPLAY_MODEL/"so101_preview.urdf"
        if path.exists() and manifest["source_sha256"] == hashlib.sha256(URDF.read_bytes()).hexdigest():
            if all((DISPLAY_MODEL/mesh).exists() for mesh in manifest["meshes"]): return path
    except (OSError,ValueError,KeyError): pass
    return URDF


class RobotMapping:
    def __init__(self, calibration=None):
        self.calibration = calibration or load_calibration()
        self.retargeter = Retargeter(URDF)
        self.limits = self.retargeter.limits
        self.current = {name:self.retargeter.neutral[name] for name in JOINT_LABELS}
        low, high = self.limits["gripper"]
        self.current["gripper"] = (low+high)/2
        self.reverse = dict.fromkeys(self.current,False)
        self.response = .035  # Preview smoothing only; physical robot limits stay unchanged.

    def targets(self, geometry, joystick, now):
        target = self.retargeter.targets(geometry,self.reverse)
        target["gripper"] = self.current["gripper"]
        if not joystick or not 0 <= now-joystick["seen"] < .4:
            return target, "Joystick stale: claw held"
        if joystick["pressed"]:
            return target, "Joystick button: claw held"
        target["gripper"] = self.calibration.position(joystick["y"], *self.limits["gripper"], reverse=self.reverse["gripper"])
        fraction = self.calibration.position(joystick["y"],0,1,reverse=self.reverse["gripper"])
        return target, f"Joystick Y={joystick['y']} -> {fraction*100:.0f}% open"

    def advance(self, target, dt):
        alpha = -math.expm1(-min(.1,max(0,dt))/self.response)
        self.current = {name:value+alpha*(target[name]-value) for name,value in self.current.items()}


def render_worker(connection):
    """CPU rendering must not block Tk or the serial command/ACK threads."""
    import pybullet as p
    client = -1
    try:
        client = p.connect(p.DIRECT)
        if client < 0:
            raise RuntimeError("Could not start the URDF renderer")
        robot = p.loadURDF(str(render_model()),useFixedBase=True,physicsClientId=client)
        joints = {}
        for index in range(p.getNumJoints(robot,physicsClientId=client)):
            info = p.getJointInfo(robot,index,physicsClientId=client)
            if info[2] != p.JOINT_FIXED:
                joints[info[1].decode()] = index
        if set(joints) != set(RobotMapping().current):
            raise ValueError("SO-101 URDF joint names do not match the mapping")
        connection.send(("ready",))
        while True:
            request = connection.recv()
            if request is None:
                break
            for joint,value in request["pose"].items():
                p.resetJointState(robot,joints[joint],value,physicsClientId=client)
            width,height = request["size"]
            distance,yaw,pitch = request["camera"]
            view = p.computeViewMatrixFromYawPitchRoll([.1,0,.12],distance,yaw,pitch,0,2)
            projection = p.computeProjectionMatrixFOV(45,width/height,.01,5)
            pixels = p.getCameraImage(width,height,viewMatrix=view,projectionMatrix=projection,
                                     renderer=p.ER_TINY_RENDERER,shadow=0,flags=p.ER_NO_SEGMENTATION_MASK,
                                     physicsClientId=client)[2]
            data = pixels.tobytes() if hasattr(pixels,"tobytes") else bytes(pixels)
            connection.send(("frame",width,height,data))
    except (EOFError,BrokenPipeError):
        pass
    except Exception as error:
        try: connection.send(("error",str(error)))
        except (OSError,EOFError): pass
    finally:
        if client >= 0:
            p.disconnect(client)
        connection.close()


class RobotPreview:
    """A second Tk view renders the actual URDF with Bullet's CPU renderer."""
    def __init__(self, parent, calibration, on_stop=None, on_recenter=None, on_pause=None):
        import tkinter as tk
        from tkinter import ttk
        from PIL import Image, ImageTk
        self.Image, self.ImageTk = Image, ImageTk
        self.mapping = RobotMapping(calibration)
        self.closed, self.running, self.valid_inputs = False, True, True
        self.stale_since = None
        self.on_stop = on_stop
        self.on_pause = on_pause
        self.last = time.monotonic()
        self.input_updated_at = self.last
        self.pose_updated_at = None
        self.pose_number = 0
        self.hold_reason = "Waiting for calibrated readings"
        # Same viewing direction as the human-arm canvas (azimuth -65, elevation 24).
        self.yaw, self.pitch, self.distance = 25., -24., .85
        self.drag_at = None
        self.transport = RenderClient(render_worker)
        self.painted_frame = 0
        self.window = tk.Toplevel(parent)
        self.window.title("SO-101 URDF — calibrated wearable preview")
        self.window.configure(bg="#0b1220")
        self.window.geometry("840x760")
        self.window.minsize(680,600)
        self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.window.bind("<Escape>",self.stop_all)
        header = ttk.Frame(self.window,padding=12)
        header.pack(fill="x")
        ttk.Label(header,text="SO-101 / URDF SIMULATION",font=("Segoe UI",15,"bold")).pack(side="left")
        self.pause_button = ttk.Button(header,text="Pause",command=self.toggle)
        self.pause_button.pack(side="right")
        ttk.Button(header,text="Restart renderer",command=self.transport.restart.set).pack(side="right",padx=6)
        if on_recenter is not None:
            ttk.Button(header,text="Recenter arm",command=on_recenter).pack(side="right")
        self.status = tk.StringVar(value="Waiting for calibrated readings")
        ttk.Label(self.window,textvariable=self.status,padding=(12,4),wraplength=790).pack(fill="x")
        self.health = tk.StringVar(value="Starting renderer…")
        health_row = ttk.Frame(self.window,padding=(12,2))
        health_row.pack(fill="x")
        self.quality = tk.StringVar(value="Fast")
        ttk.Combobox(health_row,textvariable=self.quality,values=("Fast","Balanced","Detailed"),
                     width=10,state="readonly").pack(side="right")
        ttk.Label(health_row,text="View quality ").pack(side="right")
        ttk.Label(health_row,textvariable=self.health).pack(side="left")
        self.canvas = tk.Canvas(self.window,highlightthickness=0,bg="#e9edf2")
        self.canvas.pack(fill="both",expand=True,padx=12,pady=6)
        self.image_item = self.canvas.create_image(0,0,anchor="nw")
        self.hold_background = self.canvas.create_rectangle(0,0,0,0,fill="#172135",outline="",state="hidden")
        self.hold_item = self.canvas.create_text(24,22,anchor="nw",fill="#ffcb70",font=("Segoe UI",12,"bold"),state="hidden")
        self.canvas.bind("<ButtonPress-1>",lambda event: setattr(self,"drag_at",(event.x,event.y)))
        self.canvas.bind("<B1-Motion>",self.orbit)
        self.canvas.bind("<MouseWheel>",self.zoom)
        table = ttk.Frame(self.window,padding=10)
        table.pack(fill="x")
        self.labels = {}
        for index,joint in enumerate(self.mapping.current):
            row, col = divmod(index,3)
            cell = ttk.Frame(table,padding=(8,3))
            cell.grid(row=row,column=col,sticky="ew")
            table.columnconfigure(col,weight=1)
            self.labels[joint] = ttk.Label(cell,text=JOINT_LABELS[joint])
            self.labels[joint].pack(anchor="w")
            variable = tk.BooleanVar(value=False)
            ttk.Checkbutton(cell,text="Reverse direction",variable=variable,
                            command=lambda j=joint,v=variable: self.mapping.reverse.__setitem__(j,v.get())).pack(anchor="w")
        connection_note = "Physical control uses its separate enable/stop window." if on_pause else "No physical robot connection."
        ttk.Label(self.window,text=f"Drag to orbit / scroll to zoom. Joint limits come from the URDF.\nY=0: max/open · Y={calibration.center_y:g}: halfway · Y=4095: min/closed. {connection_note}",
                  padding=(12,8),justify="left").pack(fill="x")
        self.window.after(20,self.render)

    def enable(self):
        self.running = True
        self.last = time.monotonic()
        self.window.lift()

    def calibration_changed(self):
        self.mapping.retargeter = Retargeter(URDF)
        self.last = time.monotonic()
        if self.running: self.window.lift()

    def toggle(self):
        if self.running:
            self.running = False
            if self.on_pause is not None: self.on_pause("URDF preview paused")
        elif self.valid_inputs:
            self.enable()

    def stop_all(self, event=None):
        self.running = False
        if self.on_stop is not None:
            self.on_stop()

    def update(self, geometry, joystick, calibrated, fresh, now, reason=""):
        if self.closed:
            return
        dt, self.last = now-self.last, now
        self.input_updated_at = now
        self.valid_inputs = bool(calibrated and fresh)
        hold = ""
        if not calibrated:
            self.stale_since = None
            hold = reason or "Arm reference needed: Recenter arm, or capture A/B if mounting changed."
            self.status.set(hold)
        elif not fresh:
            if self.stale_since is None: self.stale_since = now
            hold = "Waiting for IMUs: "+(reason or "data temporarily stale")
            self.status.set(hold+("; automatically resumes when fresh" if self.running else "; manually paused"))
        elif self.running:
            if self.stale_since is not None: dt = min(dt,.02)
            self.stale_since = None
            try:
                target, message = self.mapping.targets(geometry,joystick,now)
                self.mapping.advance(target,dt)
                self.pose_updated_at = now
                self.pose_number += 1
                limits = self.mapping.retargeter.limited
                self.status.set("LIVE · "+message+(" · At URDF limit: "+", ".join(limits) if limits else ""))
            except (ValueError,KeyError) as error:
                hold = f"Pose calculation: {error}; retrying with next readings"
                self.status.set(hold)
        else:
            self.stale_since = None
            hold = "Manually paused. Click Resume to follow your arm."
            self.status.set("Paused. Resume uses the current arm calibration.")
        if hold != self.hold_reason:
            logging.getLogger("wearable").info("Robot pose: %s",hold or "tracking resumed")
        self.hold_reason = hold
        self.pause_button.configure(text="Pause" if self.running else "Resume",
                                    state="normal" if self.running or self.valid_inputs else "disabled")
        for joint,value in self.mapping.current.items():
            self.labels[joint].configure(text=f"{JOINT_LABELS[joint]}  {math.degrees(value):+.1f}°")

    def orbit(self, event):
        if self.drag_at:
            self.yaw += (event.x-self.drag_at[0])*.4
            self.pitch = max(-85,min(80,self.pitch+(event.y-self.drag_at[1])*.3))
        self.drag_at = event.x,event.y

    def zoom(self, event):
        self.distance = max(.3,min(2.,self.distance*(.9 if event.delta > 0 else 1.1)))

    def render(self):
        if self.closed:
            return
        state = self.transport.snapshot()
        if state["number"] != self.painted_frame and state["frame"] is not None:
            _,width,height,data = state["frame"]
            picture = self.Image.frombytes("RGBA",(width,height),data)
            size = (max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height()))
            if picture.size != size:
                picture = picture.resize(size,self.Image.Resampling.BILINEAR)
            self.photo = self.ImageTk.PhotoImage(picture,master=self.window)
            self.canvas.itemconfigure(self.image_item,image=self.photo)
            self.painted_frame = state["number"]
        now = time.monotonic()
        age = now-state["seen"] if state["seen"] is not None else 0
        pose_age = now-self.pose_updated_at if self.pose_updated_at is not None else None
        hold = self.hold_reason
        if now-self.input_updated_at > .75:
            hold = f"Pose update loop stopped {now-self.input_updated_at:.1f}s ago. See wearable_test/runtime.log."
        pose_state = "HELD" if hold else f"{pose_age*1000:.0f} ms" if pose_age is not None else "waiting"
        self.health.set(f"Pose {pose_state} · Renderer {state['state']} · {state['fps']:.0f} fps / {age*1000:.0f} ms"+
                        (f" · {state['error']}" if state["error"] else ""))
        if hold:
            self.canvas.itemconfigure(self.hold_item,text="POSE HELD\n"+hold,width=max(100,self.canvas.winfo_width()-48),state="normal")
            box = self.canvas.bbox(self.hold_item)
            if box:
                self.canvas.coords(self.hold_background,box[0]-10,box[1]-8,box[2]+10,box[3]+8)
                self.canvas.itemconfigure(self.hold_background,state="normal")
        else:
            self.canvas.itemconfigure(self.hold_item,state="hidden")
            self.canvas.itemconfigure(self.hold_background,state="hidden")
        width,height = max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())
        budget = {"Fast":(320,200),"Balanced":(480,300),"Detailed":(640,400)}[self.quality.get()]
        factor = min(1,budget[0]/width,budget[1]/height)
        self.transport.submit({"pose":dict(self.mapping.current),"camera":(self.distance,self.yaw,self.pitch),
                               "size":(int(width*factor),int(height*factor))})
        self.window.after(16,self.render)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.on_pause is not None: self.on_pause("URDF preview closed")
        self.transport.close()
        self.window.destroy()
