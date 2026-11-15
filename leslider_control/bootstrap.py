"""Resolve drivers and calibration inside this repository."""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def initialize():
    # This affects this process only, leaving the original LeRobot cache alone.
    os.environ["HF_LEROBOT_CALIBRATION"] = str(HERE/"calibration")
    for path in (HERE.parent/"robot_control"/"vendor", HERE/"scripts"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
