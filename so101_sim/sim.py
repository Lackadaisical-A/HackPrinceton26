# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pybullet==3.2.7", "firebase-admin>=6", "pillow>=10"]
# ///
"""SO-101 URDF kinematic simulation driven by three IMUs from Firebase."""
import argparse
import json
import math
from pathlib import Path
import threading
import time
import xml.etree.ElementTree as ET

import firebase_admin
from firebase_admin import credentials, db
import pybullet as p
from PIL import Image

from mapper import Mapper, sensor_frame, slew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URDF = HERE/"model"/"so101_new_calib.urdf"


class Feed:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.error = "Connecting to Firebase"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        app = None
        try:
            folder = ROOT/"config"
            keys = list(folder.glob("*.json"))
            if len(keys) != 1:
                raise ValueError("Expected one credential JSON")
            app = firebase_admin.initialize_app(credentials.Certificate(str(keys[0])), {
                "databaseURL": (folder/"source.txt").read_text().strip(), "httpTimeout": 5,
            }, name="so101-sim")
            ref = db.reference("live/sensors", app=app)
            while not self.stop.is_set():
                try:
                    data = ref.get() or {}
                    with self.lock:
                        self.data, self.error = data, ""
                except Exception as error:
                    self.error = f"Firebase: {type(error).__name__}"
                self.stop.wait(.1)
        except Exception as error:
            self.error = f"Firebase setup: {type(error).__name__}"
        finally:
            if app:
                firebase_admin.delete_app(app)

    def snapshot(self):
        with self.lock:
            return dict(self.data), self.error


def load_model():
    for mesh in ET.parse(URDF).findall(".//mesh"):
        if not (URDF.parent/mesh.attrib["filename"]).is_file():
            raise FileNotFoundError(mesh.attrib["filename"])
    robot = p.loadURDF(str(URDF), useFixedBase=True)
    joints, limits = {}, {}
    for index in range(p.getNumJoints(robot)):
        info = p.getJointInfo(robot, index)
        if info[2] != p.JOINT_FIXED:
            name = info[1].decode()
            joints[name], limits[name] = index, (info[8], info[9])
    return robot, joints, limits


def render_check(robot, output):
    view = p.computeViewMatrixFromYawPitchRoll([0, 0, .18], .8, 130, -25, 0, 2)
    projection = p.computeProjectionMatrixFOV(45, 1.4, .01, 5)
    image = p.getCameraImage(1120, 800, view, projection, renderer=p.ER_TINY_RENDERER)
    output.parent.mkdir(parents=True, exist_ok=True)
    rgba = image[2]
    if hasattr(rgba, "reshape"):
        Image.fromarray(rgba.reshape(800, 1120, 4)).convert("RGB").save(output)
    else:
        Image.frombytes("RGBA", (1120, 800), bytes(rgba)).convert("RGB").save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Load URDF headlessly and render it; no Firebase")
    parser.add_argument("--mapping", type=Path, default=HERE/"mapping.json")
    args = parser.parse_args()
    config = json.loads(args.mapping.read_text())
    client = p.connect(p.DIRECT if args.check else p.GUI, options="--width=1280 --height=900")
    feed = None
    try:
        robot, joints, limits = load_model()
        mapper = Mapper(config, limits)
        current = {name: math.radians(spec["offset_deg"]) for name, spec in config["joints"].items()}
        for name, value in current.items():
            p.resetJointState(robot, joints[name], value)
        p.resetJointState(robot, joints["gripper"], .4)
        if args.check:
            print("URDF loaded:", joints)
            assert set(joints) == set(config["joints"]) | {"gripper"}
            render_check(robot, HERE/"preview.png")
            print("PASS: all meshes loaded; six movable joints; preview.png rendered")
            return
        p.resetDebugVisualizerCamera(.7, 130, -25, [-.05, 0, .18])
        p.configureDebugVisualizer(p.COV_ENABLE_KEYBOARD_SHORTCUTS, 0)
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        p.configureDebugVisualizer(p.COV_ENABLE_SHADOWS, 1)
        gripper = .4
        p.addUserDebugText("C: calibrate | SPACE: pause | G: gripper | Q: quit", [.25, .2, .45], [0, 0, 0], 1.1)
        p.addUserDebugText("SO-101 kinematic preview", [.25, .2, .42], [.6, .1, .1], 1.1)
        for axis, color in [([.15,0,0],[1,0,0]), ([0,.15,0],[0,1,0]), ([0,0,.15],[0,0,1])]:
            p.addUserDebugLine([0,0,0], axis, color, 2)
        feed = Feed()
        enabled = False
        calibrated_hub = None
        was_stale = False
        text_id = -1
        previous = time.monotonic()
        next_text = 0
        print("Keep the dashboard running. Mount sensors, point arm forward, then press C in this simulation.", flush=True)
        while p.isConnected():
            now = time.monotonic()
            dt, previous = now-previous, now
            events = p.getKeyboardEvents()
            if events.get(ord('q'), 0) & p.KEY_WAS_TRIGGERED:
                break
            data, error = feed.snapshot()
            frame = None
            try:
                if error:
                    raise ValueError(error)
                frame = sensor_frame(data, config, time.time_ns()//1_000_000)
                active_hub = data[config["sensors"]["upper_arm"]].get("hub_id")
                if mapper.neutral is not None and active_hub != calibrated_hub:
                    mapper.neutral = None
                    enabled = False
                message = "Live inputs ready - press C to capture neutral" if mapper.neutral is None else ("LIVE IMU CONTROL" if enabled else "PAUSED - SPACE to resume / C to recenter")
                was_stale = False
            except ValueError as problem:
                message = str(problem)
                enabled = False
                if not was_stale:
                    print("Holding pose:", message, flush=True)
                was_stale = True
            if frame and events.get(ord('c'), 0) & p.KEY_WAS_TRIGGERED:
                mapper.calibrate(frame)
                calibrated_hub = active_hub
                enabled = True
                print("Neutral captured; simulation enabled", flush=True)
            if frame and mapper.neutral and events.get(ord(' '), 0) & p.KEY_WAS_TRIGGERED:
                enabled = not enabled
            if enabled and frame:
                current = slew(current, mapper.targets(frame), math.radians(config["max_speed_deg_s"]), dt)
                for name, value in current.items():
                    p.resetJointState(robot, joints[name], value)
            if events.get(ord('g'), 0) & p.KEY_WAS_TRIGGERED:
                gripper = 1.2 if gripper < .7 else 0.0
            p.resetJointState(robot, joints["gripper"], gripper)
            if now >= next_text:
                text_id = p.addUserDebugText(message, [.25, .2, .39], [0, .3, .5], 1, replaceItemUniqueId=text_id)
                next_text = now+.25
            time.sleep(1/60)
    finally:
        if feed:
            feed.stop.set()
            feed.thread.join(timeout=6)
        if p.isConnected(client):
            p.disconnect(client)


if __name__ == "__main__":
    main()
