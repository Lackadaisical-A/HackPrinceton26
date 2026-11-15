# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4", "huggingface-hub>=0.34,<2", "pyzmq>=26", "pyyaml>=6", "websockets>=15"]
# ///
"""Run the copied controllers without the original LeRobot checkout."""
import importlib
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from leslider_control.bootstrap import HERE, initialize


COMMANDS = {
    "pots": "esp32_wh148_leader_bridge",
    "leslider": "esp32_wh148_leslider_7dof_bridge",
    "slider": "keyboard_slider_control",
    "calibrate-slider": "calibrate_slider_motor_only",
}


def main():
    initialize()
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    remaining = sys.argv[2:]
    if command in ("help", "--help", "-h"):
        print("Usage: uv run --python 3.12 leslider_control/run.py COMMAND [controller options]")
        print("Commands: pots (six pots), leslider (seven pots), slider (ID-7 tester),")
        print("          calibrate-slider (manual endpoints), ports, test, demo (vision + web + pots)")
        print("Use COMMAND --help for that controller's options.")
        return 0
    if command == "ports":
        from serial.tools.list_ports import comports
        for port in comports():
            print(f"{port.device}: {port.description}")
        return 0
    if command == "test":
        return subprocess.call([sys.executable, "-m", "pytest", str(HERE/"tests"), "-q", *remaining], cwd=HERE.parent)
    if command == "demo":
        from leslider_control.session import main as demo
        return demo(remaining)
    if command not in COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    sys.argv = [COMMANDS[command]+".py", *remaining]
    return importlib.import_module(COMMANDS[command]).main()


if __name__ == "__main__":
    raise SystemExit(main())
