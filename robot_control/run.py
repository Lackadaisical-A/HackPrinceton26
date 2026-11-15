# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "firebase-admin>=6", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4"]
# ///
"""SO-101 motor-load haptics, three IMUs, and optional arm/joystick claw control."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"bno085_reader"))

from robot_control.arm import Arm, JOINTS, choose_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Robot port; auto-detects one extra USB serial device if omitted")
    parser.add_argument("--joint", choices=JOINTS, default="shoulder_pan")
    parser.add_argument("--reverse-load", action="store_true", help="Swap the left/right pulse labels")
    parser.add_argument("--deadband", type=float, default=25)
    parser.add_argument("--full-scale", type=float, default=95)
    parser.add_argument("--wroom-port", default="COM7")
    parser.add_argument("--s3-port", default="COM8")
    parser.add_argument("--joystick-axis", choices=["x", "y"], default="y")
    parser.add_argument("--reverse-claw", action="store_true", help="Reverse joystick open/close direction")
    parser.add_argument("--check", action="store_true", help="Read all six motors once; no writes or haptics")
    parser.add_argument("--no-firebase", action="store_true")
    args = parser.parse_args()
    try:
        port = choose_port(args.port, (args.wroom_port, args.s3_port))
        if args.check:
            print(f"Checking SO-101 on {port} (read only)")
            arm = Arm(port)
            try:
                arm.connect()
                for name in JOINTS:
                    print(f"{name}: load={arm.read_load(name):+d}, position="
                          f"{arm.bus.read('Present_Position', name, normalize=False)}, torque="
                          f"{arm.bus.read('Torque_Enable', name, normalize=False)}")
                arm.validate_motion()
                arm.validate_gripper()
                print("Saved homing offsets, position modes, and current joint ranges match hardware")
                print("PASS: six SO-101 motors responded; no robot settings or positions changed")
            finally:
                arm.close()
            return 0
        from tkinter import ttk
        from dashboard import Dashboard, Hub
        from robot_control.controller import RobotWorker
        hub = Hub(args.wroom_port, args.s3_port, cloud=not args.no_firebase)
        worker = RobotWorker(hub, port, args.joint, args.reverse_load, args.deadband, args.full_scale,
                             args.joystick_axis, args.reverse_claw)
        hub.haptic_source = worker.haptic_output
        hub.robot_source = worker.telemetry
        app = Dashboard(hub, start=False)
        app.root.title("SO-101 | Motor-load feedback | Three IMUs")
        for panel in app.panels.values():
            panel.canvas.configure(height=300)
        row = ttk.Frame(app.root, padding=8)
        row.pack(fill="x", before=app.root.winfo_children()[1])
        ttk.Button(row, text="Hold current pose (load test)", command=lambda: worker.commands.put("hold")).pack(side="left", padx=4)
        ttk.Button(row, text="Zero load at rest", command=lambda: worker.commands.put("zero")).pack(side="left", padx=4)
        ttk.Button(row, text="Capture neutral + enable IMU arm control", command=lambda: worker.commands.put("enable")).pack(side="left", padx=4)
        ttk.Button(row, text="Pause arm + claw", command=lambda: worker.commands.put("pause")).pack(side="left", padx=4)
        claw_row = ttk.Frame(app.root, padding=8)
        claw_row.pack(fill="x", after=row)
        ttk.Button(claw_row, text="Enable claw position control (neutral = half open)",
                   command=lambda: worker.commands.put("claw_enable")).pack(side="left", padx=4)
        ttk.Button(claw_row, text="Pause claw", command=lambda: worker.commands.put("claw_pause")).pack(side="left", padx=4)
        label = ttk.Label(app.root, padding=8)
        label.pack(fill="x", before=row)
        def refresh():
            label.config(text=worker.status+"\n"+worker.motion_status+"\n"+worker.claw.status)
            app.root.after(100, refresh)
        refresh()
        hub.start()
        worker.thread.start()
        print("Motor-load mode: IMU tilt does not drive the coin. Enable feedback in the window.", flush=True)
        print("Arm starts read-only. IMU control requires the Capture neutral button.", flush=True)
        print("Release the joystick, then enable claw position control. Y=0 open, Y=4095 closed; neutral=half open. Button pauses.", flush=True)
        try:
            app.root.mainloop()
        finally:
            hub.close()
            deadline = time.monotonic()+7
            for thread in [worker.thread, *hub.threads]:
                thread.join(timeout=max(0, deadline-time.monotonic()))
        return 0
    except (ValueError, ConnectionError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
