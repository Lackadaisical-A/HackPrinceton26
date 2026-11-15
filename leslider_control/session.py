"""Bring up vision + web console around the existing interactive pot controller."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen
import webbrowser

import yaml
from serial.tools.list_ports import comports
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", choices=("pots", "leslider"), default="leslider",
                        help="leslider matches esp32_wh148_slider_7dof_bridge.py; pots is six channels only")
    parser.add_argument("--leader-port", default="COM9")
    parser.add_argument("--robot-port", default="COM6")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source", default="wrist", help="Vision camera index, wrist, or video path")
    parser.add_argument("--vision-config", type=Path, default=ROOT / "computer_vision" / "config.yaml")
    parser.add_argument("--stream-port", type=int, default=18766, help="Avoids this machine's occupied port 8766")
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--robot-pub-port", type=int, default=5558)
    parser.add_argument("--check", action="store_true", help="Check dependencies and ports; do not open hardware")
    parser.add_argument("--services-only", action="store_true", help="Run vision and console without a pot controller")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("controller_args", nargs=argparse.REMAINDER, help="Controller options after --")
    args = parser.parse_args(argv)
    for key in ("stream_port", "web_port", "bridge_port", "robot_pub_port"):
        if not 1 <= getattr(args, key) <= 65535:
            parser.error(f"--{key.replace('_', '-')} must be in 1..65535")
    if len({args.stream_port, args.web_port, args.bridge_port, args.robot_pub_port}) != 4:
        parser.error("Service ports must be different")
    if args.leader_port.upper() == args.robot_port.upper():
        parser.error("Leader and robot ports must be different")
    if args.controller_args[:1] == ["--"]:
        args.controller_args = args.controller_args[1:]
    reserved = ("--auto-enable", "--leader-port", "--robot-port", "--telemetry-endpoint")
    if any(item.split("=")[0] in reserved for item in args.controller_args):
        parser.error("Set ports through the launcher; integrated sessions always start paused")
    return args


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def ready(kind, port):
    try:
        if kind == "bridge":
            with connect(f"ws://127.0.0.1:{port}", origin="http://127.0.0.1:5173", open_timeout=1, close_timeout=0.2):
                return True
        path = "/stream" if kind == "vision" else "/"
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            if kind == "vision":
                return "multipart/x-mixed-replace" in response.headers.get("Content-Type", "")
            return b"/src/main.tsx" in response.read(65536)
    except (OSError, ValueError, TimeoutError):
        return False
    except Exception:
        if kind == "bridge":
            return False
        raise


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only our service process tree. The motor controller runs in this foreground process.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def ensure_service(stack, kind, port, command, cwd, log_dir):
    if listening(port):
        if not ready(kind, port):
            raise RuntimeError(f"Port {port} is occupied but is not a ready {kind} service")
        print(f"Reusing {kind} on port {port}; its existing configuration stays active.")
        return
    log_path = log_dir / f"{kind}.log"
    output = stack.enter_context(log_path.open("w", encoding="utf-8"))
    child = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    stack.callback(stop_child, child)
    print(f"Starting {kind}; log: {log_path}")
    deadline = time.monotonic() + 90
    report_at = time.monotonic() + 15
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"{kind} exited ({child.returncode}); see {log_path}")
        if ready(kind, port):
            return
        if time.monotonic() >= report_at:
            print(f"Waiting for {kind} to start; see {log_path}")
            report_at += 15
        time.sleep(0.2)
    raise RuntimeError(f"{kind} did not start within 90 seconds; see {log_path}")


def session_config(config_path, stream_port):
    config_path = config_path.resolve()
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg["detector"]["models_dir"] = str((config_path.parent / cfg["detector"]["models_dir"]).resolve())
    cfg["stream"].update(enabled=True, host="127.0.0.1", port=stream_port)
    return cfg


def controller_argv(args):
    result = ["--leader-port", args.leader_port, "--robot-port", args.robot_port,
              "--telemetry", "--telemetry-endpoint", f"tcp://127.0.0.1:{args.robot_pub_port}"]
    if args.dry_run:
        result.append("--dry-run")
    return result + args.controller_args


def main(argv=None):
    args = parse_args(argv)
    vision_python = ROOT / "computer_vision" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    ports = {port.device.upper(): port.description for port in comports()}
    print("Serial ports: " + ", ".join(f"{port} ({name})" for port, name in ports.items()))
    issues = []
    if not vision_python.exists():
        issues.append("Prepare computer_vision/.venv first (see computer_vision/README.md for the GPU extra).")
    if not vite.exists() or not shutil.which("node"):
        issues.append("Install Node and run npm --prefix frontend ci first.")
    if not args.services_only:
        for port in [args.leader_port] + ([] if args.dry_run else [args.robot_port]):
            if port.upper() not in ports:
                issues.append(f"{port} is not connected. Connect it or supply the correct port.")
        if listening(args.robot_pub_port):
            issues.append(f"Robot telemetry port {args.robot_pub_port} is occupied; stop the other controller first.")
    try:
        cfg = session_config(args.vision_config, args.stream_port)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        issues.append(f"Cannot load vision config: {exc}")
        cfg = None
    for kind, port in [("vision", args.stream_port), ("bridge", args.bridge_port), ("frontend", args.web_port)]:
        occupied = listening(port)
        valid = occupied and ready(kind, port)
        print(f"{kind}: {'running' if valid else 'port occupied' if occupied else 'will start'} (port {port})")
        if occupied and not valid:
            issues.append(f"Port {port} is occupied by an unexpected or unready {kind} service.")
    for issue in issues:
        print(issue, file=sys.stderr)
    if issues or args.check:
        return 2 if issues else 0

    log_dir = ROOT / "runs" / "pot-test" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log_dir.mkdir(parents=True)
    config_path = log_dir / "vision.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    camera_url = f"http://127.0.0.1:{args.stream_port}/stream"
    ws_url = f"ws://127.0.0.1:{args.bridge_port}"
    url = f"http://127.0.0.1:{args.web_port}/?" + urlencode({"source": "live", "wsUrl": ws_url, "cameraUrl": camera_url}) + "#/"
    (log_dir / "session.json").write_text(json.dumps({"url": url, "controller": args.controller,
                                                    "dry_run": args.dry_run, "leader_port": args.leader_port,
                                                    "robot_port": args.robot_port}, indent=2), encoding="utf-8")
    try:
        with ExitStack() as stack:
            bridge_command = [sys.executable, "-u", "-m", "paul_bridge", "--listen", f"127.0.0.1:{args.bridge_port}",
                              "--commands", cfg["transport"]["command_sub"]]
            for endpoint in [cfg["transport"]["vision_pub"], cfg["transport"]["pressure_sub"],
                             f"tcp://127.0.0.1:{args.robot_pub_port}"]:
                bridge_command += ["--from", endpoint]
            ensure_service(stack, "bridge", args.bridge_port, bridge_command, ROOT / "bridge", log_dir)
            ensure_service(stack, "vision", args.stream_port,
                           [str(vision_python), "-u", "-m", "paul_vision", "--config", str(config_path),
                            "--source", args.source, "--no-overlay"], ROOT / "computer_vision", log_dir)
            ensure_service(stack, "frontend", args.web_port,
                           [shutil.which("node"), str(vite), "--host", "127.0.0.1", "--port", str(args.web_port), "--strictPort"],
                           ROOT / "frontend", log_dir)
            print(f"\nConsole: {url}\nVision helps the operator; it does not command arm motion.")
            if not args.no_browser:
                webbrowser.open(url)
            if args.services_only:
                print("Services only. Ctrl+C stops services started by this session.")
                while True:
                    time.sleep(0.5)
            print("\nSupport the arm before startup: the existing controller disables torque.")
            print("Focus this terminal for E (enable/pause), T (torque off), Q (quit).\n")
            module = "esp32_wh148_leader_bridge" if args.controller == "pots" else "esp32_wh148_slider_7dof_bridge"
            previous = sys.argv
            try:
                sys.argv = [module, *controller_argv(args)]
                return importlib.import_module(module).main()
            finally:
                sys.argv = previous
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"Session stopped: {exc}", file=sys.stderr)
        return 1

