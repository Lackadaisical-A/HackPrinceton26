#!/usr/bin/env python
r"""Control an SO-101 plus a seventh position servo from seven WH148 pots.

Default ESP32 mapping::

    GPIO32  shoulder_pan
    GPIO33  shoulder_lift
    GPIO34  elbow_flex
    GPIO35  wrist_flex
    GPIO25  wrist_roll
    GPIO26  gripper
    GPIO27  slider

The slider servo is motor ID 7 on the follower's Feetech bus. The current
so101_slider_follower adapter uses calibrated multi-turn position feedback
and presents the measured rail travel as ``slider.pos`` in the range 0..100.
Its session-reference and Phase-28 metadata checks are preserved.

Typical use from the repository root::

    .\control.ps1 leslider -LeaderPort COM9 -RobotPort COM6

The default ports are COM7 for the ESP32 and COM6 for the follower.  The first
run learns a separate seven-pot calibration and, if needed, interactively
calibrates only the new slider axis.  Press ``e`` to enable motion.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import socket
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from leslider_control.telemetry import RobotTelemetry, add_telemetry_args, disconnect_if_connected

import esp32_wh148_leader_bridge as bridge
from so101_phase_utils import add_elbow_phase_arg, apply_elbow_phase
from so101_slider_follower import (
    ALL_MOTORS,
    SLIDER,
    SO101SliderFollower,
    SO101SliderFollowerConfig,
    calibrate_slider_motor_only,
    load_arm_calibration,
    save_combined_slider_calibration,
    setup_slider_motor_id,
    slider_calibration_path,
    stock_arm_calibration_path,
)

BODY_MOTORS = tuple(bridge.BODY_MOTORS)
ROBOT_MOTORS = tuple(ALL_MOTORS)  # Canonical dataset/policy order: arm, gripper, slider.
CHANNEL_MOTORS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
    SLIDER,
)
DEFAULT_PINS = ("32", "33", "34", "35", "25", "26", "27")
DEFAULT_CALIBRATION_FILE = (
    Path(__file__).resolve().parents[1] / "config" / "wh148_slider_7dof_pot_calibration.json"
)

# Compatibility exports for older local callers; the current adapter handles
# calibrated multi-turn travel with session-reference validation.
SO101LeSliderPositionConfig = SO101SliderFollowerConfig
SO101LeSliderPositionFollower = SO101SliderFollower


def default_channels() -> list[bridge.Channel]:
    return [
        bridge.Channel(pin=pin, motor=motor) for pin, motor in zip(DEFAULT_PINS, CHANNEL_MOTORS, strict=True)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seven-pot absolute controller for an SO-101 plus slider servo ID 7."
    )
    parser.add_argument("--leader-port", default="COM7", help="ESP32 port. Default: COM7.")
    parser.add_argument("--robot-port", default="COM6", help="SO-101/slider bus port. Default: COM6.")
    parser.add_argument("--robot-id", default="my_so101_follower")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Read and map pots without opening COM6.")

    parser.add_argument(
        "--setup-slider-id",
        action="store_true",
        help="Set the single attached slider servo to --slider-id, then exit.",
    )
    parser.add_argument("--slider-id", type=int, default=7)
    parser.add_argument("--slider-initial-id", type=int)
    parser.add_argument("--slider-initial-baudrate", type=int)
    parser.add_argument("--slider-range-min", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--slider-goal-speed", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--calibrate-slider-only",
        "--calibrate-robot-only",
        dest="calibrate_slider_only",
        action="store_true",
        help="Manually record slider ID 7 endpoints with torque off; preserve arm calibration.",
    )

    parser.add_argument("--calibration-file", type=Path, default=DEFAULT_CALIBRATION_FILE)
    parser.add_argument("--recalibrate", action="store_true", help="Learn all seven pot ranges again.")
    parser.add_argument(
        "--recalibrate-only",
        default="",
        help="Comma-separated motor names or pins to relearn while keeping the other ranges.",
    )
    parser.add_argument("--no-save-calibration", action="store_true")
    parser.add_argument(
        "--autocalib-seconds",
        type=float,
        default=0.0,
        help="Default 0: press e to start pot-range capture and e again to finish.",
    )
    parser.add_argument("--min-pot-range", type=float, default=50.0)
    parser.set_defaults(gripper_input="pot")
    parser.add_argument(
        "--invert",
        default=None,
        help="Comma-separated motor names or GPIO pins to reverse; omitted uses saved values.",
    )

    parser.add_argument("--control-mode", choices=("relative", "absolute"), default="absolute")
    parser.add_argument("--body-span", type=float, default=30.0)
    parser.add_argument("--slider-span", type=float, default=35.0)
    parser.add_argument("--gripper-span", type=float, default=35.0)
    parser.add_argument("--deadband", type=float, default=0.5)
    parser.add_argument(
        "--fast-control",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the 50 Hz control default. Enabled by default.",
    )
    parser.add_argument("--max-rate", type=float, default=None)
    parser.add_argument("--max-relative-target", type=float, default=10.0)
    parser.add_argument(
        "--max-enable-error",
        type=float,
        default=0.0,
        help="Absolute startup alignment guard. Default 0 disables it.",
    )
    parser.add_argument("--stale-seconds", type=float, default=0.5)
    parser.add_argument(
        "--auto-enable",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable torque as soon as all seven channels arrive. Default is to wait for e.",
    )

    parser.add_argument("--rviz", action="store_true", help="Publish the six visible arm joints to RViz.")
    parser.add_argument("--rviz-target-ip", default="auto")
    parser.add_argument("--rviz-target-port", type=int, default=bridge.DEFAULT_RVIZ_PORT)
    parser.add_argument("--wsl-distro", default="Ubuntu-24.04")
    add_elbow_phase_arg(parser)
    add_telemetry_args(parser)
    return parser.parse_args()


def joint_limit(robot: SO101SliderFollower, motor: str) -> tuple[float, float]:
    if motor in {SLIDER, "gripper"}:
        return 0.0, 100.0
    return bridge.robot_joint_limits(robot, motor)


def dry_run_positions() -> dict[str, float]:
    return {motor: (50.0 if motor in {SLIDER, "gripper"} else 0.0) for motor in ROBOT_MOTORS}


def dry_run_joint_limits() -> dict[str, tuple[float, float]]:
    limits = {
        motor: tuple(math.degrees(value) for value in bridge.URDF_LIMITS_RAD[motor]) for motor in BODY_MOTORS
    }
    limits["gripper"] = (0.0, 100.0)
    limits[SLIDER] = (0.0, 100.0)
    return limits


def target_spans(args: argparse.Namespace) -> dict[str, float]:
    spans = dict.fromkeys(BODY_MOTORS, args.body_span)
    spans[SLIDER] = args.slider_span
    spans["gripper"] = args.gripper_span
    return spans


def robot_from_args(args: argparse.Namespace, max_relative_target: float) -> SO101SliderFollower:
    return SO101SliderFollower(
        SO101SliderFollowerConfig(
            port=args.robot_port,
            id=args.robot_id,
            max_relative_target=dict.fromkeys(ROBOT_MOTORS, max_relative_target),
            slider_id=args.slider_id,
        )
    )


def print_help() -> None:
    print(
        """
SO-101 + slider seven-pot controls

  e      enable / pause motor streaming
  c      recenter relative mapping
  t      disable torque immediately
  p      print potentiometers and targets
  h      show this help
  q/ESC  quit and disable torque

GPIO27 is slider left/right. GPIO34 is restored to elbow flex.
"""
    )


def _setup_slider(args: argparse.Namespace) -> int:
    print("IMPORTANT: disconnect IDs 1..6 and connect ONLY the new slider servo to the controller.")
    input(f"Press ENTER to set that one servo to ID {args.slider_id}, or Ctrl+C to cancel....")
    setup_slider_motor_id(
        args.robot_port,
        slider_id=args.slider_id,
        initial_id=args.slider_initial_id,
        initial_baudrate=args.slider_initial_baudrate,
    )
    print(f"Slider motor ID is now {args.slider_id}. Power off before reconnecting the full daisy chain.")
    return 0


def _calibrate_slider_only(args: argparse.Namespace) -> int:
    arm_path = stock_arm_calibration_path(args.robot_id)
    arm_calibration = load_arm_calibration(arm_path)
    output_path = slider_calibration_path(args.robot_id)
    print(f"Reading IDs 1..6 calibration from disk only: {arm_path}")
    print(f"The motor bus for this operation contains only slider ID {args.slider_id}.")
    slider_calibration = calibrate_slider_motor_only(
        args.robot_port,
        slider_id=args.slider_id,
    )
    saved_path = save_combined_slider_calibration(
        args.robot_id,
        arm_calibration,
        slider_calibration,
        output_path,
    )
    print(f"Seven-axis calibration saved to {saved_path}")
    return 0


def main() -> int:
    args = parse_args()
    logging.getLogger().setLevel(logging.ERROR)

    if args.list_ports:
        bridge.list_serial_ports()
        return 0
    if args.setup_slider_id:
        return _setup_slider(args)
    if args.calibrate_slider_only:
        return _calibrate_slider_only(args)

    channels = default_channels()
    pins = list(DEFAULT_PINS)
    channel_motors = list(CHANNEL_MOTORS)
    try:
        recalibrate_channels = bridge.parse_channel_selection(args.recalibrate_only, channels)
        if args.recalibrate and recalibrate_channels:
            raise ValueError("Use either --recalibrate or --recalibrate-only, not both.")
        if args.invert is None and args.calibration_file.exists():
            inverted_motors = bridge.load_saved_inverted_motors(args.calibration_file, channels)
        else:
            inverted_motors = bridge.parse_inverted(args.invert or "", channels)
        max_rate, max_relative_target = bridge.resolve_control_speed(
            args.fast_control, args.max_rate, args.max_relative_target
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(exc)
        return 2

    if not 1 <= args.rviz_target_port <= 65535:
        print("--rviz-target-port must be between 1 and 65535.")
        return 2

    rviz_target: tuple[str, int] | None = None
    if args.rviz:
        try:
            rviz_ip = (
                bridge.detect_wsl_ip(args.wsl_distro)
                if args.rviz_target_ip.lower() == "auto"
                else args.rviz_target_ip
            )
        except RuntimeError as exc:
            print(exc)
            return 2
        rviz_target = (rviz_ip, args.rviz_target_port)

    print(f"Opening seven-pot ESP32 leader on {args.leader_port} @ {args.baud}...")
    try:
        leader_serial = bridge.serial.Serial(args.leader_port, args.baud, timeout=0.03)
    except bridge.serial.SerialException as exc:
        bridge.print_serial_open_error("seven-pot ESP32 leader", args.leader_port, exc)
        return 1

    interval = 1.0 / max_rate
    spans = target_spans(args)
    with ExitStack() as stack:
        serial_port = stack.enter_context(leader_serial)
        telemetry = stack.enter_context(RobotTelemetry(args.telemetry_endpoint)) if args.telemetry else None
        rviz_socket: socket.socket | None = None
        if rviz_target is not None:
            rviz_socket = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            print(f"RViz arm stream: udp://{rviz_target[0]}:{rviz_target[1]} (slider is not in the arm URDF)")

        time.sleep(2.0)
        serial_port.reset_input_buffer()
        try:
            calibrations = bridge.make_pot_calibrations(
                args,
                serial_port,
                channels,
                recalibrate_channels,
                inverted_motors,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(exc)
            return 2

        robot: SO101SliderFollower | None = None
        current_positions = dry_run_positions()
        if args.dry_run:
            joint_limits = dry_run_joint_limits()
            print("Dry run: COM6 will not be opened and no motors will move.")
        else:
            robot = robot_from_args(args, max_relative_target)
            stack.callback(disconnect_if_connected, robot)
            if not robot.is_calibrated:
                print(
                    "Seven-axis calibration is missing or invalid. Run "
                    "./control.ps1 calibrate-slider first."
                )
                return 2
            try:
                print(f"Connecting SO-101 + slider on {args.robot_port}...")
                robot.connect(calibrate=False)
            except (ConnectionError, bridge.serial.SerialException) as exc:
                with suppress(Exception):
                    if robot.bus.is_connected:
                        robot.disconnect()
                print(f"Could not connect the seven-axis follower on {args.robot_port}: {exc}")
                return 1
            apply_elbow_phase(
                robot,
                args.elbow_phase,
                torque_limit=args.elbow_torque_limit,
                startup_force=args.elbow_startup_force,
                p_coefficient=args.elbow_p_coefficient,
            )
            current_positions = bridge.obs_to_positions(robot.get_observation(), channels)
            joint_limits = {motor: joint_limit(robot, motor) for motor in ROBOT_MOTORS}
            robot.bus.disable_torque()
            print("Seven-axis torque is disabled. Press e to enable control.")

        center_pots = {pin: calibrations[pin].center for pin in pins}
        center_targets = {
            motor: current_positions[motor]
            if args.control_mode == "relative"
            else sum(joint_limits[motor]) / 2
            for motor in ROBOT_MOTORS
        }
        latest_values: dict[str, float] = {}
        latest_times: dict[str, float] = {}
        latest_targets: dict[str, float] = {}
        last_sent_targets: dict[str, float] = {}
        last_send_t = 0.0
        last_rviz_t = 0.0
        rviz_sequence = 0
        last_print_t = 0.0
        enabled = False
        auto_enable_pending = args.auto_enable

        def set_enabled() -> bool:
            nonlocal center_pots, center_targets, enabled, last_sent_targets, latest_targets
            missing = bridge.missing_channels(latest_values, channels) or bridge.stale_channels(latest_times, channels, args.stale_seconds, time.perf_counter())
            if missing:
                print("Cannot enable; waiting for " + ", ".join(f"{c.pin}/{c.motor}" for c in missing))
                return False
            errors: dict[str, float] = {}
            if robot is not None:
                present = bridge.obs_to_positions(robot.get_observation(), channels)
                if args.control_mode == "relative":
                    center_targets = present
                else:
                    errors = bridge.excessive_enable_errors(present, latest_targets, args.max_enable_error)
            if errors:
                print(
                    "Cannot enable; target errors: "
                    + " | ".join(f"{motor}:{error:+.2f}" for motor, error in errors.items())
                )
                return False
            if args.control_mode == "relative":
                center_pots = {pin: latest_values[pin] for pin in pins}
                latest_targets = {}
            if robot is not None:
                robot.bus.enable_torque()
            last_sent_targets = {}
            enabled = True
            print("\nSeven-axis streaming ENABLED.")
            return True

        print_help()
        print("Pin mapping: " + " | ".join(f"{item.pin}->{item.motor}" for item in channels))
        print(
            f"mode={args.control_mode} rate={max_rate:.0f}Hz tracking_horizon={max_relative_target:g} "
            f"startup_guard={args.max_enable_error:g} slider_mode=bounded-position"
        )

        try:
            while True:
                parsed, _line = bridge.read_pot_line(serial_port, pins)
                now = time.perf_counter()
                if parsed:
                    for pin, value in parsed.items():
                        latest_values[pin] = value
                        latest_times[pin] = now
                    latest_targets = bridge.compute_targets(
                        latest_values,
                        channels,
                        calibrations,
                        center_pots,
                        center_targets,
                        joint_limits,
                        spans,
                        inverted_motors,
                        args.control_mode,
                        "pot",
                        0,
                        100.0,
                        0.0,
                    )

                if auto_enable_pending and len(latest_targets) == len(channels):
                    auto_enable_pending = False
                    set_enabled()

                key = bridge.read_key()
                if key in {"q", "\x1b"}:
                    print("\nQuitting.")
                    break
                if key == "h":
                    print_help()
                elif key == "e":
                    if enabled:
                        enabled = False
                        if robot is not None:
                            robot.bus.disable_torque()
                        print("\nStreaming PAUSED; torque disabled.")
                    else:
                        set_enabled()
                elif key == "c":
                    if not bridge.missing_channels(latest_values, channels):
                        if robot is not None:
                            center_targets = bridge.obs_to_positions(robot.get_observation(), channels)
                        center_pots = {pin: latest_values[pin] for pin in pins}
                        last_sent_targets = {}
                        latest_targets = {}
                        print("\nRelative mapping recentered.")
                elif key == "t":
                    enabled = False
                    if robot is not None:
                        robot.bus.disable_torque()
                    print("\nTorque disabled.")
                elif key == "p":
                    print("\nPots:", bridge.format_values(latest_values, pins))
                    print("Targets:", bridge.format_targets(latest_targets, channels))

                if enabled:
                    stale = bridge.stale_channels(latest_times, channels, args.stale_seconds, now)
                    if stale:
                        enabled = False
                        if robot is not None:
                            robot.bus.disable_torque()
                        print(
                            "\nStale ESP32 data for "
                            + ", ".join(f"{item.pin}/{item.motor}" for item in stale)
                            + "; torque disabled."
                        )

                changed = (
                    any(
                        motor not in last_sent_targets
                        or abs(latest_targets[motor] - last_sent_targets[motor]) >= args.deadband
                        for motor in channel_motors
                    )
                    if len(latest_targets) == len(channels)
                    else False
                )
                if enabled and changed and now - last_send_t >= interval:
                    if robot is not None:
                        sent = robot.send_action(bridge.action_from_targets(latest_targets))
                        last_sent_targets = {
                            key.removesuffix(".pos"): float(value) for key, value in sent.items()
                        }
                    else:
                        last_sent_targets = dict(latest_targets)
                    last_send_t = now

                if (
                    rviz_socket is not None
                    and rviz_target is not None
                    and enabled
                    and len(latest_targets) == len(channels)
                    and now - last_rviz_t >= interval
                ):
                    payload = bridge.rviz_payload(
                        rviz_sequence,
                        last_sent_targets or latest_targets,
                        joint_limits,
                    )
                    with suppress(OSError):
                        rviz_socket.sendto(json.dumps(payload).encode("utf-8"), rviz_target)
                    rviz_sequence += 1
                    last_rviz_t = now

                if telemetry is not None:
                    telemetry.update(robot, targets=latest_targets, enabled=enabled,
                                     input_fresh=not (bridge.missing_channels(latest_values, channels) or bridge.stale_channels(latest_times, channels, args.stale_seconds, now)))

                if now - last_print_t >= 0.25:
                    state = "ENABLED" if enabled else "PAUSED"
                    status = (
                        bridge.format_targets(latest_targets, channels)
                        if len(latest_targets) == len(channels)
                        else "waiting for all seven potentiometers"
                    )
                    print(f"\r{state} | {status}", end="", flush=True)
                    last_print_t = now
                time.sleep(0.002)
        finally:
            print()
            if robot is not None and robot.is_connected:
                robot.disconnect()
                print("Disconnected; all seven motor torques disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
