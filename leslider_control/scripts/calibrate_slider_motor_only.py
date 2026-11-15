#!/usr/bin/env python
r"""Manually calibrate only slider servo ID 7; preserve SO-101 IDs 1..6.

Run from the repository root::

    .\control.ps1 calibrate-slider -RobotPort COM6

The procedure disables ID 7 torque, enables multi-turn position feedback, and
waits for you to place the slider at each endpoint. It never enables torque and
never writes Goal_Position. Keep servo power connected while moving between the
two endpoints so every motor revolution is counted. The six original arm
calibration entries are copied unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from so101_slider_follower import (
    calibrate_slider_motor_only,
    load_arm_calibration,
    save_combined_slider_calibration,
    slider_calibration_path,
    stock_arm_calibration_candidates,
    stock_arm_calibration_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record multi-turn slider ID 7 endpoints by hand with torque off; preserve arm IDs 1..6."
        )
    )
    parser.add_argument("--port", default="COM6", help="Follower motor bus. Default: COM6.")
    parser.add_argument("--robot-id", default="my_so101_follower")
    parser.add_argument(
        "--arm-calibration",
        type=Path,
        help="Existing six-axis calibration JSON. By default it is found from --robot-id.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Combined seven-axis output. Defaults to the safe slider follower calibration cache.",
    )
    parser.add_argument("--slider-id", type=int, default=7)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    arm_path = args.arm_calibration or stock_arm_calibration_path(args.robot_id)
    if not arm_path.is_file():
        searched = "\n  ".join(str(path) for path in stock_arm_calibration_candidates(args.robot_id))
        raise FileNotFoundError(
            f"No six-axis calibration was found for robot ID {args.robot_id!r}. Searched:\n  {searched}"
        )
    arm_calibration = load_arm_calibration(arm_path)
    output_path = args.output or slider_calibration_path(args.robot_id)

    print(f"Preserving IDs 1..6 exactly from: {arm_path}")
    print(f"Opening only slider motor ID {args.slider_id} on {args.port}.")
    print("ID 7 torque stays OFF. No Goal_Position command is sent.")
    print("Position feedback is active: keep COM6 and servo power connected between both endpoints.")
    slider_calibration = calibrate_slider_motor_only(args.port, slider_id=args.slider_id)
    saved_path = save_combined_slider_calibration(
        args.robot_id,
        arm_calibration,
        slider_calibration,
        output_path,
    )
    endpoint_zero = (
        slider_calibration.range_min if slider_calibration.drive_mode == 0 else slider_calibration.range_max
    )
    endpoint_hundred = (
        slider_calibration.range_max if slider_calibration.drive_mode == 0 else slider_calibration.range_min
    )
    print(f"Slider calibrated: raw {endpoint_zero} -> slider.pos=0")
    print(f"Slider calibrated: raw {endpoint_hundred} -> slider.pos=100")
    print(
        f"Recorded travel: {slider_calibration.range_max - slider_calibration.range_min} ticks "
        f"({(slider_calibration.range_max - slider_calibration.range_min) / 4096:.2f} motor turns)"
    )
    print(f"Saved combined calibration: {saved_path}")
    backup = saved_path.with_suffix(saved_path.suffix + ".bak")
    if backup.is_file():
        print(f"Previous combined calibration backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
