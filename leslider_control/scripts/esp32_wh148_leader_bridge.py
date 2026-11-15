#!/usr/bin/env python
r"""Bridge a six-pot WH148 ESP32 leader arm to one SO-101 follower.

Run from PowerShell after activating the LeRobot venv:
    .\control.ps1 pots -LeaderPort COM9 -RobotPort COM6

For an RViz-only dry run (start the ROS launch described in the package README first):
    uv run --python 3.12 leslider_control/run.py pots --leader-port COM9 --dry-run --rviz \
        --control-mode absolute --recalibrate

Default ESP32 GPIO pin order for your ESP32 wiring:
    32 shoulder_pan
    33 shoulder_lift
    34 elbow_flex
    35 wrist_flex
    25 wrist_roll
    26 gripper

Accepted ESP32 serial line examples:
    {"values":{"32":1234,"33":1220,"34":2100,"35":990,"25":3020,"26":1400}}
    {"filtered":[1234,1220,2100,990,3020,1400]}
    32=1234 33=1220 34=2100 35=990 25=3020 26=1400
    1234,1220,2100,990,3020,1400

The original Thingiverse leader_arm.ino emits five pot readings plus a D2 gripper
button as CSV. Use --gripper-input button for that sketch.

The script starts paused. Press e to enable streaming, t to disable torque,
c to recenter relative control, and q to quit.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import socket
import subprocess
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import serial
from serial.tools import list_ports

try:
    import msvcrt
except ImportError:  # pragma: no cover - the hardware bridge normally runs on Windows
    msvcrt = None

from so101_phase_utils import add_elbow_phase_arg, apply_elbow_phase

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from leslider_control.telemetry import RobotTelemetry, add_telemetry_args, disconnect_if_connected

BODY_MOTORS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
MOTORS = [*BODY_MOTORS, "gripper"]
DEFAULT_PINS = ["32", "33", "34", "35", "25", "26"]
DEFAULT_CALIBRATION_FILE = (
    Path(__file__).resolve().parents[1] / "config" / "wh148_leader_pot_calibration.json"
)
DEFAULT_RVIZ_PORT = 50103
SAFE_MAX_RATE = 25.0
SAFE_MAX_RELATIVE_TARGET = 4.0
FAST_MAX_RATE = 50.0
FAST_MAX_RELATIVE_TARGET = 8.0

# Keep these synchronized with so101_6dof_rviz.urdf. Dry-run targets use the
# equivalent values in degrees, while UDP packets contain ROS JointState radians.
URDF_LIMITS_RAD = {
    "shoulder_pan": (-1.91986, 1.91986),
    "shoulder_lift": (-1.74533, 1.74533),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.65806, 1.65806),
    "wrist_roll": (-2.74385, 2.84121),
    "gripper": (-0.174533, 1.74533),
}

JSON_PREFIXES = ("CALIB_JSON ", "POT_JSON ")
PIN_VALUE_RE = re.compile(r"\b([A-Za-z_]*\d+|[A-Za-z_]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)\b")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Channel:
    pin: str
    motor: str


@dataclass
class PotCalibration:
    low: float
    center: float
    high: float

    @property
    def span(self) -> float:
        return self.high - self.low


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use a six-pot WH148 ESP32 leader arm with an SO-101 follower."
    )
    parser.add_argument("--leader-port", help="ESP32 serial port, for example COM7.")
    parser.add_argument("--robot-port", help="SO-101 follower port, for example COM6.")
    parser.add_argument("--robot-id", default="my_so101_follower")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read pots and print targets without connecting the robot.",
    )
    parser.add_argument(
        "--rviz",
        action="store_true",
        help="Also stream the mapped target pose to the SO-101 RViz UDP receiver.",
    )
    parser.add_argument(
        "--rviz-target-ip",
        default="auto",
        help="RViz/ROS host IP, or 'auto' to detect the selected WSL distro.",
    )
    parser.add_argument(
        "--rviz-target-port",
        type=int,
        default=DEFAULT_RVIZ_PORT,
        help=f"RViz UDP receiver port. Default: {DEFAULT_RVIZ_PORT}.",
    )
    parser.add_argument(
        "--wsl-distro",
        default="Ubuntu-24.04",
        help="WSL distro used by automatic RViz IP detection.",
    )

    parser.add_argument(
        "--pins",
        default=",".join(DEFAULT_PINS),
        help="Comma-separated ESP32 GPIO pins in motor order. Default: 32,33,34,35,25,26.",
    )
    parser.add_argument(
        "--motors",
        default=",".join(MOTORS),
        help="Comma-separated SO-101 motors in pin order. Default: shoulder_pan,...,gripper.",
    )
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=DEFAULT_CALIBRATION_FILE,
        help="JSON file for saved WH148 pot min/center/max calibration.",
    )
    parser.add_argument(
        "--recalibrate",
        action="store_true",
        help="Ignore saved pot calibration and learn a new range before connecting the robot.",
    )
    parser.add_argument(
        "--recalibrate-only",
        default="",
        help=(
            "Comma-separated motors or GPIO pins to recalibrate while preserving all other saved ranges, "
            "for example gripper or shoulder_pan,26."
        ),
    )
    parser.add_argument(
        "--no-save-calibration",
        action="store_true",
        help="Do not write newly learned pot calibration to --calibration-file.",
    )
    parser.add_argument(
        "--autocalib-seconds",
        type=float,
        default=0.0,
        help=(
            "Optional timed WH148 calibration. The default 0 uses manual control: press e to start "
            "collecting min/max values and press e again to finish."
        ),
    )
    parser.add_argument(
        "--min-pot-range",
        type=float,
        default=50.0,
        help="Minimum ADC range each pot must cover during calibration. Use 0 to disable.",
    )

    parser.add_argument(
        "--control-mode",
        choices=("relative", "absolute"),
        default="relative",
        help="relative maps pots around the current robot pose; absolute maps pots to joint limits.",
    )
    parser.add_argument(
        "--gripper-input",
        choices=("pot", "button"),
        default="pot",
        help="Use pot for a sixth analog gripper channel, or button for the original D2 gripper switch.",
    )
    parser.add_argument(
        "--button-pressed-value",
        type=int,
        choices=(0, 1),
        default=0,
        help="Digital value that means the gripper button is pressed. INPUT_PULLUP sketches usually use 0.",
    )
    parser.add_argument(
        "--button-open-target",
        type=float,
        default=100.0,
        help="SO-101 gripper target when the button is not pressed.",
    )
    parser.add_argument(
        "--button-closed-target",
        type=float,
        default=0.0,
        help="SO-101 gripper target when the button is pressed.",
    )
    parser.add_argument(
        "--invert",
        default=None,
        help=(
            "Comma-separated motors or pins to reverse, for example shoulder_pan,26. Use all to reverse all. "
            "If omitted, use inversions saved in the calibration file."
        ),
    )
    parser.add_argument(
        "--body-span",
        type=float,
        default=30.0,
        help="Relative-mode half-span for body joints, in degrees.",
    )
    parser.add_argument(
        "--gripper-span",
        type=float,
        default=35.0,
        help="Relative-mode half-span for gripper, in 0-100 units.",
    )
    parser.add_argument("--deadband", type=float, default=0.5, help="Minimum target change before sending.")
    parser.add_argument(
        "--fast-control",
        action="store_true",
        help=(
            f"Use faster defaults ({FAST_MAX_RATE:.0f} Hz and {FAST_MAX_RELATIVE_TARGET:.0f} motor-unit "
            "tracking horizon). Explicit --max-rate/--max-relative-target values still win."
        ),
    )
    parser.add_argument(
        "--max-rate",
        type=float,
        default=None,
        help=(
            f"Maximum command rate. Default: {SAFE_MAX_RATE:.0f} Hz, or {FAST_MAX_RATE:.0f} Hz with "
            "--fast-control."
        ),
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=0.5,
        help="Disable torque if any channel stops updating for this long while streaming.",
    )
    parser.add_argument(
        "--max-relative-target",
        type=float,
        default=None,
        help=(
            f"LeRobot tracking horizon for every motor. Default: {SAFE_MAX_RELATIVE_TARGET:.0f}, or "
            f"{FAST_MAX_RELATIVE_TARGET:.0f} with --fast-control. Lower is safer; higher follows faster."
        ),
    )
    parser.add_argument(
        "--max-enable-error",
        type=float,
        default=10.0,
        help=(
            "Refuse to enable real-arm absolute mode when any leader target differs from the present "
            "joint by more than this many motor units. Use 0 to disable the guard."
        ),
    )
    add_elbow_phase_arg(parser)
    add_telemetry_args(parser)
    return parser.parse_args()


def list_serial_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    for port in ports:
        desc = port.description or ""
        hwid = port.hwid or ""
        print(f"{port.device:8s} {desc} {hwid}")


def detect_wsl_ip(distro: str) -> str:
    """Return the first address reported by the requested WSL distribution."""
    try:
        result = subprocess.run(
            ["wsl.exe", "-d", distro, "--", "hostname", "-I"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"Could not detect an IP for WSL distro '{distro}'. "
            "Start the distro or pass --rviz-target-ip explicitly."
        ) from exc

    ips = [part.strip() for part in result.stdout.split() if part.strip()]
    if not ips:
        raise RuntimeError(f"WSL distro '{distro}' did not report an IP. Pass --rviz-target-ip explicitly.")
    return ips[0]


def print_serial_open_error(role: str, port: str, exc: serial.SerialException) -> None:
    print(f"Could not open {role} port {port}: {exc}")
    print("On Windows, access denied usually means another program already has that COM port open.")
    print("Close Arduino Serial Monitor/Plotter, VS Code serial monitor, PuTTY, and any previous Python run.")
    print("Then unplug/replug the board and verify the port with --list-ports.")
    print("Available ports:")
    list_serial_ports()


def parse_csv(value: str, label: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError(f"--{label} must not be empty.")
    return items


def make_channels(args: argparse.Namespace) -> list[Channel]:
    pins = parse_csv(args.pins, "pins")
    motors = parse_csv(args.motors, "motors")
    if len(pins) != len(motors):
        raise ValueError(f"--pins has {len(pins)} entries but --motors has {len(motors)} entries.")
    unknown = [motor for motor in motors if motor not in MOTORS]
    if unknown:
        raise ValueError(f"Unknown SO-101 motor(s): {', '.join(unknown)}. Choices: {', '.join(MOTORS)}")
    if len(set(pins)) != len(pins):
        raise ValueError("--pins contains duplicate GPIO pins.")
    if len(set(motors)) != len(motors):
        raise ValueError("--motors contains duplicate motors.")
    return [Channel(pin=pin, motor=motor) for pin, motor in zip(pins, motors, strict=True)]


def parse_inverted(value: str, channels: list[Channel]) -> set[str]:
    tokens = {token.strip() for token in value.split(",") if token.strip()}
    if not tokens:
        return set()
    motors = {channel.motor for channel in channels}
    pins = {channel.pin for channel in channels}
    if "all" in tokens:
        tokens.remove("all")
        inverted = set(motors)
    else:
        inverted = set()

    unknown: list[str] = []
    for token in tokens:
        if token in motors:
            inverted.add(token)
        elif token in pins:
            inverted.add(next(channel.motor for channel in channels if channel.pin == token))
        else:
            unknown.append(token)
    if unknown:
        raise ValueError(f"Unknown --invert value(s): {', '.join(unknown)}. Use motor names, pins, or all.")
    return inverted


def parse_channel_selection(value: str, channels: list[Channel]) -> list[Channel]:
    tokens = {token.strip() for token in value.split(",") if token.strip()}
    if not tokens:
        return []
    if "all" in tokens:
        if len(tokens) > 1:
            raise ValueError("Use either 'all' or individual values with --recalibrate-only, not both.")
        return list(channels)

    selected = [channel for channel in channels if channel.motor in tokens or channel.pin in tokens]
    matched = {channel.motor for channel in selected} | {channel.pin for channel in selected}
    unknown = sorted(token for token in tokens if token not in matched)
    if unknown:
        raise ValueError(
            f"Unknown --recalibrate-only value(s): {', '.join(unknown)}. Use motor names, pins, or all."
        )
    return selected


def strip_json_prefix(line: str) -> str:
    line = line.strip()
    for prefix in JSON_PREFIXES:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return line


def canonical_pin(key: object) -> str:
    text = str(key).strip()
    match = re.search(r"\d+", text)
    return match.group(0) if match else text


def values_from_sequence(values: object, pins: list[str]) -> dict[str, float]:
    if not isinstance(values, list | tuple):
        return {}
    if len(values) < len(pins):
        return {}
    return {pin: float(values[idx]) for idx, pin in enumerate(pins)}


def values_from_mapping(values: object, pins: list[str]) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    pin_set = set(pins)
    parsed: dict[str, float] = {}
    for key, value in values.items():
        pin = canonical_pin(key)
        if pin in pin_set:
            parsed[pin] = float(value)
    return parsed


def parse_pot_values(line: str, pins: list[str]) -> dict[str, float]:
    line = strip_json_prefix(line)
    if not line:
        return {}

    if line.startswith("{") or line.startswith("["):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return values_from_sequence(data, pins)
        if isinstance(data, dict):
            for key in ("filtered", "raw", "values", "adc", "pins"):
                nested = data.get(key)
                parsed = values_from_mapping(nested, pins) or values_from_sequence(nested, pins)
                if parsed:
                    return parsed
            return values_from_mapping(data, pins)

    pin_set = set(pins)
    parsed = {}
    for key, value in PIN_VALUE_RE.findall(line):
        pin = canonical_pin(key)
        if pin in pin_set:
            parsed[pin] = float(value)
    if parsed:
        return parsed

    if "=" not in line and ":" not in line:
        numbers = [float(match.group(0)) for match in NUMBER_RE.finditer(line)]
        if len(numbers) >= len(pins):
            return {pin: numbers[idx] for idx, pin in enumerate(pins)}

    return {}


def read_pot_line(ser: serial.Serial, pins: list[str]) -> tuple[dict[str, float], str]:
    raw = ser.readline()
    if not raw:
        return {}, ""
    line = raw.decode("utf-8", errors="ignore").strip()
    return parse_pot_values(line, pins), line


def plain_csv_value_count(line: str) -> int | None:
    """Return the numeric field count for a plain CSV sample, or None otherwise."""
    fields = [field.strip() for field in line.strip().split(",")]
    if len(fields) < 2 or any(not field for field in fields):
        return None
    try:
        for field in fields:
            float(field)
    except ValueError:
        return None
    return len(fields)


def is_gripper_button_channel(channel: Channel, gripper_input: str) -> bool:
    return gripper_input == "button" and channel.motor == "gripper"


def is_digital_sample(value: float) -> bool:
    return abs(value - round(value)) <= 0.01 and int(round(value)) in (0, 1)


def learn_pot_calibrations(
    ser: serial.Serial,
    channels: list[Channel],
    seconds: float,
    min_pot_range: float,
    gripper_input: str,
    calibration_channels: list[Channel] | None = None,
) -> dict[str, PotCalibration]:
    pins = [channel.pin for channel in channels]
    calibration_channels = channels if calibration_channels is None else calibration_channels
    values: dict[str, list[float]] = {channel.pin: [] for channel in calibration_channels}
    last_serial_line = ""
    repeated_short_csv_count = 0
    last_short_csv_width: int | None = None
    selected_names = ", ".join(channel.motor for channel in calibration_channels)
    interactive = seconds <= 0
    if interactive:
        print(f"Ready to learn the WH148 range for {selected_names}.")
        print("Press e to START collecting min/max values (q or ESC cancels).")
        while True:
            key = read_key()
            if key == "e":
                break
            if key in {"q", "\x1b"}:
                raise RuntimeError("Potentiometer calibration cancelled before collection started.")
            time.sleep(0.01)
        if hasattr(ser, "reset_input_buffer"):
            ser.reset_input_buffer()
        print("Collecting continuously. Move the selected pot(s) through their full range.")
        print("Press e again to FINISH (q or ESC cancels).")
    else:
        print(f"Learning WH148 range for {selected_names} for {seconds:.1f}s.")
        print("Move only the selected joint(s) through their full safe range now.")
    print(
        "Calibrating:",
        " | ".join(f"{channel.pin}:{channel.motor}" for channel in calibration_channels),
    )

    deadline = None if interactive else time.perf_counter() + seconds
    last_print = 0.0
    while deadline is None or time.perf_counter() < deadline:
        parsed, line = read_pot_line(ser, pins)
        if line:
            last_serial_line = line
            csv_width = plain_csv_value_count(line)
            if csv_width is not None and csv_width < len(pins):
                if csv_width == last_short_csv_width:
                    repeated_short_csv_count += 1
                else:
                    last_short_csv_width = csv_width
                    repeated_short_csv_count = 1
                if repeated_short_csv_count >= 3:
                    raise RuntimeError(
                        f"ESP32 firmware mismatch: received {csv_width} CSV values per frame, but this "
                        f"controller requires {len(pins)} ({','.join(pins)}). Upload the matching "
                        "multi-pot firmware before calibrating."
                    )
            elif csv_width is not None:
                repeated_short_csv_count = 0
                last_short_csv_width = None
        now = time.perf_counter()
        for channel in calibration_channels:
            if channel.pin in parsed:
                values[channel.pin].append(parsed[channel.pin])
        if now - last_print >= 0.2:
            if any(values.values()):
                parts = []
                for channel in calibration_channels:
                    samples = values[channel.pin]
                    if samples:
                        parts.append(
                            f"{channel.motor}={samples[-1]:.0f}[{min(samples):.0f},{max(samples):.0f}]"
                        )
                    else:
                        parts.append(f"{channel.motor}=waiting")
                print("\r" + " | ".join(parts), end="", flush=True)
            else:
                print(
                    f"\rWaiting for a valid {len(pins)}-value frame ({','.join(pins)})...",
                    end="",
                    flush=True,
                )
            last_print = now
        if interactive:
            key = read_key()
            if key == "e":
                break
            if key in {"q", "\x1b"}:
                print()
                raise RuntimeError("Potentiometer calibration cancelled; saved ranges were not changed.")
    print()

    if not any(values.values()):
        if "raw=" in last_serial_line or "filtered=" in last_serial_line:
            raise RuntimeError(
                f"The ESP32 is sending a single-pot calibration stream, not {len(channels)} joint values. "
                "Upload the matching multi-pot firmware, then retry. "
                f"Last serial line: {last_serial_line[:180]}"
            )
        if last_serial_line:
            raise RuntimeError(
                f"Serial data arrived, but it was not a supported {len(channels)}-channel format. "
                f"Expected {len(channels)} CSV values in GPIO order {','.join(pins)}. "
                f"Last serial line: {last_serial_line[:180]}"
            )
        raise RuntimeError(
            "No serial data arrived from the ESP32. Check --leader-port, close other serial monitors, "
            "and confirm the firmware uses 115200 baud."
        )

    calibrations: dict[str, PotCalibration] = {}
    for channel in calibration_channels:
        samples = values[channel.pin]
        if len(samples) < 5:
            raise RuntimeError(f"Not enough samples for GPIO {channel.pin} / {channel.motor}.")
        if is_gripper_button_channel(channel, gripper_input):
            bad_samples = [value for value in samples if not is_digital_sample(value)]
            if bad_samples:
                raise RuntimeError(
                    f"GPIO {channel.pin} / gripper was configured as a button, "
                    f"but received non-digital value {bad_samples[0]:.1f}."
                )
            calibrations[channel.pin] = PotCalibration(low=0.0, center=0.5, high=1.0)
            continue
        low = min(samples)
        high = max(samples)
        if min_pot_range > 0 and high - low < min_pot_range:
            raise RuntimeError(
                f"GPIO {channel.pin} / {channel.motor} range is too small: "
                f"low={low:.1f}, high={high:.1f}. Check wiring or move the joint farther."
            )
        calibrations[channel.pin] = PotCalibration(low=low, center=(low + high) / 2, high=high)
    return calibrations


def load_pot_calibrations(path: Path, channels: list[Channel]) -> dict[str, PotCalibration]:
    data = json.loads(path.read_text(encoding="utf-8"))
    source = data.get("calibrations", data)
    calibrations: dict[str, PotCalibration] = {}
    missing: list[str] = []
    for channel in channels:
        entry = source.get(channel.pin)
        if entry is None:
            missing.append(channel.pin)
            continue
        calibrations[channel.pin] = PotCalibration(
            low=float(entry["low"]),
            center=float(entry.get("center", (float(entry["low"]) + float(entry["high"])) / 2)),
            high=float(entry["high"]),
        )
    if missing:
        raise RuntimeError(f"Calibration file {path} is missing GPIO pin(s): {', '.join(missing)}")
    return calibrations


def load_saved_inverted_motors(path: Path, channels: list[Channel]) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    saved = data.get("inverted_motors", [])
    if not isinstance(saved, list):
        raise RuntimeError(f"Calibration file {path} has an invalid inverted_motors value.")
    return parse_inverted(",".join(str(value) for value in saved), channels)


def save_pot_calibrations(
    path: Path,
    channels: list[Channel],
    calibrations: dict[str, PotCalibration],
    inverted_motors: set[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "pins": [channel.pin for channel in channels],
        "motors": [channel.motor for channel in channels],
        "inverted_motors": [
            channel.motor for channel in channels if channel.motor in (inverted_motors or set())
        ],
        "input_modes": {
            channel.pin: "button" if calibrations[channel.pin].span == 1.0 else "pot" for channel in channels
        },
        "calibrations": {
            channel.pin: {
                "motor": channel.motor,
                "low": calibrations[channel.pin].low,
                "center": calibrations[channel.pin].center,
                "high": calibrations[channel.pin].high,
            }
            for channel in channels
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def make_pot_calibrations(
    args: argparse.Namespace,
    ser: serial.Serial,
    channels: list[Channel],
    recalibrate_channels: list[Channel] | None = None,
    inverted_motors: set[str] | None = None,
) -> dict[str, PotCalibration]:
    recalibrate_channels = recalibrate_channels or []
    if recalibrate_channels:
        if not args.calibration_file.exists():
            raise RuntimeError(
                f"Cannot use --recalibrate-only without an existing calibration file: {args.calibration_file}"
            )
        calibrations = load_pot_calibrations(args.calibration_file, channels)
        updated = learn_pot_calibrations(
            ser,
            channels,
            args.autocalib_seconds,
            args.min_pot_range,
            args.gripper_input,
            calibration_channels=recalibrate_channels,
        )
        calibrations.update(updated)
        preserved_count = len(channels) - len(recalibrate_channels)
        print(f"Preserved {preserved_count} saved joint calibration(s).")
    elif args.calibration_file.exists() and not args.recalibrate:
        calibrations = load_pot_calibrations(args.calibration_file, channels)
        print(f"Loaded WH148 calibration from {args.calibration_file}")
        return calibrations
    else:
        calibrations = learn_pot_calibrations(
            ser,
            channels,
            args.autocalib_seconds,
            args.min_pot_range,
            args.gripper_input,
        )
    if not args.no_save_calibration:
        save_pot_calibrations(args.calibration_file, channels, calibrations, inverted_motors)
        print(f"Saved WH148 calibration to {args.calibration_file}")
    return calibrations


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def normalized_absolute(value: float, calib: PotCalibration, invert: bool) -> float:
    norm = (value - calib.low) / max(1.0, calib.span)
    norm = clamp(norm, 0.0, 1.0)
    return 1.0 - norm if invert else norm


def normalized_centered(value: float, calib: PotCalibration, invert: bool) -> float:
    if value >= calib.center:
        denom = max(1.0, calib.high - calib.center)
        norm = (value - calib.center) / denom
    else:
        denom = max(1.0, calib.center - calib.low)
        norm = (value - calib.center) / denom
    norm = clamp(norm, -1.0, 1.0)
    return -norm if invert else norm


def robot_joint_limits(robot: SO101Follower, motor: str) -> tuple[float, float]:
    if motor == "gripper":
        return 0.0, 100.0

    calibration = robot.bus.calibration[motor]
    model = robot.bus.motors[motor].model
    max_res = robot.bus.model_resolution_table[model] - 1
    mid = (calibration.range_min + calibration.range_max) / 2
    low = (calibration.range_min - mid) * 360 / max_res
    high = (calibration.range_max - mid) * 360 / max_res
    return float(low), float(high)


def obs_to_positions(obs: dict[str, float], channels: list[Channel]) -> dict[str, float]:
    return {channel.motor: float(obs[f"{channel.motor}.pos"]) for channel in channels}


def dry_run_positions(channels: list[Channel]) -> dict[str, float]:
    positions = {}
    for channel in channels:
        positions[channel.motor] = 50.0 if channel.motor == "gripper" else 0.0
    return positions


def dry_run_joint_limits(channels: list[Channel]) -> dict[str, tuple[float, float]]:
    limits: dict[str, tuple[float, float]] = {}
    for channel in channels:
        if channel.motor == "gripper":
            limits[channel.motor] = (0.0, 100.0)
        else:
            urdf_low, urdf_high = URDF_LIMITS_RAD[channel.motor]
            limits[channel.motor] = (math.degrees(urdf_low), math.degrees(urdf_high))
    return limits


def targets_to_urdf_radians(
    targets: dict[str, float],
    joint_limits: dict[str, tuple[float, float]],
) -> dict[str, float]:
    """Map LeRobot-space targets to the matching position in the SO-101 URDF."""
    positions: dict[str, float] = {}
    for motor in MOTORS:
        if motor not in targets or motor not in joint_limits:
            continue
        target_low, target_high = joint_limits[motor]
        if target_high <= target_low:
            raise ValueError(f"Invalid target limits for {motor}: {target_low}, {target_high}")
        ratio = clamp((targets[motor] - target_low) / (target_high - target_low), 0.0, 1.0)
        urdf_low, urdf_high = URDF_LIMITS_RAD[motor]
        positions[motor] = urdf_low + ratio * (urdf_high - urdf_low)
    return positions


def rviz_payload(
    sequence: int,
    targets: dict[str, float],
    joint_limits: dict[str, tuple[float, float]],
) -> dict[str, object]:
    return {
        "source": "esp32_wh148_leader",
        "seq": sequence,
        "stamp": time.time(),
        "positions_rad": targets_to_urdf_radians(targets, joint_limits),
    }


def target_spans(args: argparse.Namespace, channels: list[Channel]) -> dict[str, float]:
    return {
        channel.motor: args.gripper_span if channel.motor == "gripper" else args.body_span
        for channel in channels
    }


def resolve_control_speed(
    fast_control: bool,
    max_rate: float | None,
    max_relative_target: float | None,
) -> tuple[float, float]:
    resolved_rate = max_rate if max_rate is not None else (FAST_MAX_RATE if fast_control else SAFE_MAX_RATE)
    resolved_target = (
        max_relative_target
        if max_relative_target is not None
        else (FAST_MAX_RELATIVE_TARGET if fast_control else SAFE_MAX_RELATIVE_TARGET)
    )
    if resolved_rate <= 0:
        raise ValueError("--max-rate must be greater than zero.")
    if resolved_target <= 0:
        raise ValueError("--max-relative-target must be greater than zero.")
    return resolved_rate, resolved_target


def compute_targets(
    latest_values: dict[str, float],
    channels: list[Channel],
    calibrations: dict[str, PotCalibration],
    center_pots: dict[str, float],
    center_targets: dict[str, float],
    joint_limits: dict[str, tuple[float, float]],
    spans: dict[str, float],
    inverted_motors: set[str],
    control_mode: str,
    gripper_input: str,
    button_pressed_value: int,
    button_open_target: float,
    button_closed_target: float,
) -> dict[str, float]:
    targets: dict[str, float] = {}
    for channel in channels:
        if channel.pin not in latest_values:
            continue
        motor = channel.motor
        joint_low, joint_high = joint_limits[motor]
        value = latest_values[channel.pin]
        invert = motor in inverted_motors
        if is_gripper_button_channel(channel, gripper_input):
            if not is_digital_sample(value):
                continue
            pressed = int(round(value)) == button_pressed_value
            if invert:
                pressed = not pressed
            target = button_closed_target if pressed else button_open_target
            targets[motor] = clamp(target, joint_low, joint_high)
            continue
        if control_mode == "absolute":
            norm = normalized_absolute(value, calibrations[channel.pin], invert)
            targets[motor] = joint_low + norm * (joint_high - joint_low)
        else:
            calib = calibrations[channel.pin]
            live_calib = PotCalibration(low=calib.low, center=center_pots[channel.pin], high=calib.high)
            norm = normalized_centered(value, live_calib, invert)
            targets[motor] = clamp(center_targets[motor] + norm * spans[motor], joint_low, joint_high)
    return targets


def action_from_targets(targets: dict[str, float]) -> dict[str, float]:
    return {f"{motor}.pos": value for motor, value in targets.items()}


def excessive_enable_errors(
    current_positions: dict[str, float],
    targets: dict[str, float],
    max_error: float,
) -> dict[str, float]:
    if max_error <= 0:
        return {}
    return {
        motor: targets[motor] - current_positions[motor]
        for motor in targets.keys() & current_positions.keys()
        if abs(targets[motor] - current_positions[motor]) > max_error
    }


def print_help() -> None:
    print(
        """
WH148 ESP32 leader bridge

Keys in this PowerShell window:
  e      enable / pause streaming
  c      recenter: current leader pose maps to current robot pose
  t      disable torque immediately
  p      print current pot/target state
  h      show help
  q/ESC  quit and disable torque

Start with the arm clear, one hand near the power switch, and streaming paused.
"""
    )


def read_key() -> str | None:
    if msvcrt is None or not msvcrt.kbhit():
        return None
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        _ = msvcrt.getwch()
        return None
    return key.lower()


def format_values(values: dict[str, float], names: list[str], width: int = 7) -> str:
    parts = []
    for name in names:
        value = values.get(name)
        parts.append(f"{name}:{'--':>{width}}" if value is None else f"{name}:{value:{width}.1f}")
    return " | ".join(parts)


def format_targets(targets: dict[str, float], channels: list[Channel]) -> str:
    return " | ".join(
        f"{channel.motor}:{targets[channel.motor]:7.2f}"
        if channel.motor in targets
        else f"{channel.motor}:     --"
        for channel in channels
    )


def missing_channels(latest_values: dict[str, float], channels: list[Channel]) -> list[Channel]:
    return [channel for channel in channels if channel.pin not in latest_values]


def stale_channels(
    latest_times: dict[str, float],
    channels: list[Channel],
    stale_seconds: float,
    now: float,
) -> list[Channel]:
    if stale_seconds <= 0:
        return []
    return [
        channel
        for channel in channels
        if channel.pin not in latest_times or now - latest_times[channel.pin] > stale_seconds
    ]


def main() -> int:
    args = parse_args()
    logging.getLogger().setLevel(logging.ERROR)

    try:
        channels = make_channels(args)
        recalibrate_channels = parse_channel_selection(args.recalibrate_only, channels)
        if args.recalibrate and recalibrate_channels:
            raise ValueError("Use either --recalibrate or --recalibrate-only, not both.")
    except ValueError as exc:
        print(exc)
        return 2

    pins = [channel.pin for channel in channels]
    motors = [channel.motor for channel in channels]

    if args.list_ports:
        list_serial_ports()
        return 0

    try:
        if args.invert is None and args.calibration_file.exists():
            inverted_motors = load_saved_inverted_motors(args.calibration_file, channels)
        else:
            inverted_motors = parse_inverted(args.invert or "", channels)
    except (OSError, RuntimeError, ValueError) as exc:
        print(exc)
        return 2

    if not args.leader_port:
        print("Missing --leader-port. Available ports:")
        list_serial_ports()
        return 2
    if not args.dry_run and not args.robot_port:
        print("Missing --robot-port, for example --robot-port COM6.")
        return 2
    if args.rviz and set(motors) != set(MOTORS):
        print("--rviz requires all six SO-101 motors in --motors.")
        return 2
    if not 1 <= args.rviz_target_port <= 65535:
        print("--rviz-target-port must be between 1 and 65535.")
        return 2
    try:
        max_rate, max_relative_target = resolve_control_speed(
            args.fast_control,
            args.max_rate,
            args.max_relative_target,
        )
    except ValueError as exc:
        print(exc)
        return 2

    rviz_target: tuple[str, int] | None = None
    if args.rviz:
        try:
            rviz_ip = (
                detect_wsl_ip(args.wsl_distro)
                if args.rviz_target_ip.lower() == "auto"
                else args.rviz_target_ip
            )
        except RuntimeError as exc:
            print(exc)
            return 2
        rviz_target = (rviz_ip, args.rviz_target_port)

    interval = 1.0 / max_rate
    spans = target_spans(args, channels)

    print(f"Opening WH148 ESP32 leader on {args.leader_port} @ {args.baud}...")
    try:
        leader_serial = serial.Serial(args.leader_port, args.baud, timeout=0.03)
    except serial.SerialException as exc:
        print_serial_open_error("WH148 ESP32 leader", args.leader_port, exc)
        return 1

    with ExitStack() as stack:
        ser = stack.enter_context(leader_serial)
        telemetry = stack.enter_context(RobotTelemetry(args.telemetry_endpoint)) if args.telemetry else None
        rviz_socket: socket.socket | None = None
        if rviz_target is not None:
            rviz_socket = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            print(f"RViz target stream: udp://{rviz_target[0]}:{rviz_target[1]}")

        # ESP32 often resets when the serial port opens.
        time.sleep(2.0)
        ser.reset_input_buffer()
        pot_calibrations = make_pot_calibrations(
            args,
            ser,
            channels,
            recalibrate_channels,
            inverted_motors,
        )

        robot: SO101Follower | None = None
        joint_limits: dict[str, tuple[float, float]]
        current_robot_positions = dry_run_positions(channels)

        if not args.dry_run:
            print(f"Connecting to SO-101 follower on {args.robot_port} with id '{args.robot_id}'...")
            robot = SO101Follower(
                SO101FollowerConfig(
                    port=args.robot_port,
                    id=args.robot_id,
                    max_relative_target={channel.motor: max_relative_target for channel in channels},
                )
            )
            stack.callback(disconnect_if_connected, robot)
            try:
                robot.connect()
            except serial.SerialException as exc:
                print_serial_open_error("SO-101 follower", args.robot_port, exc)
                return 1
            apply_elbow_phase(
                robot,
                args.elbow_phase,
                torque_limit=args.elbow_torque_limit,
                startup_force=args.elbow_startup_force,
                p_coefficient=args.elbow_p_coefficient,
            )
            current_robot_positions = obs_to_positions(robot.get_observation(), channels)
            joint_limits = {channel.motor: robot_joint_limits(robot, channel.motor) for channel in channels}
            robot.bus.disable_torque()
            print("Robot torque disabled. Press e to enable streaming.")
        else:
            joint_limits = dry_run_joint_limits(channels)
            print("Dry run: robot will not be connected or commanded. Using SO-101 URDF limits.")

        print(
            "Target limits:",
            " | ".join(
                f"{motor}=[{joint_limits[motor][0]:.2f},{joint_limits[motor][1]:.2f}]" for motor in motors
            ),
        )

        center_pots = {pin: pot_calibrations[pin].center for pin in pins}
        center_targets = {
            motor: current_robot_positions[motor]
            if args.control_mode == "relative"
            else (joint_limits[motor][0] + joint_limits[motor][1]) / 2
            for motor in motors
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

        print_help()
        print("Pin mapping:", " | ".join(f"{channel.pin}->{channel.motor}" for channel in channels))
        print("Inverted motors:", ", ".join(sorted(inverted_motors)) if inverted_motors else "none")
        print(
            f"Mode={args.control_mode} body_span={args.body_span:.2f} gripper_span={args.gripper_span:.2f} "
            f"max_rate={max_rate:.1f}Hz tracking_horizon={max_relative_target:.2f} "
            f"fast_control={args.fast_control} stale={args.stale_seconds:.2f}s"
        )

        try:
            while True:
                parsed, _line = read_pot_line(ser, pins)
                now = time.perf_counter()
                if parsed:
                    for pin, value in parsed.items():
                        latest_values[pin] = value
                        latest_times[pin] = now
                    latest_targets = compute_targets(
                        latest_values,
                        channels,
                        pot_calibrations,
                        center_pots,
                        center_targets,
                        joint_limits,
                        spans,
                        inverted_motors,
                        args.control_mode,
                        args.gripper_input,
                        args.button_pressed_value,
                        args.button_open_target,
                        args.button_closed_target,
                    )

                key = read_key()
                if key in ("q", "\x1b"):
                    print("\nQuitting.")
                    break
                if key == "h":
                    print_help()
                elif key == "e":
                    if enabled:
                        enabled = False
                        if robot is not None:
                            robot.bus.disable_torque()
                        print("Streaming PAUSED. Torque disabled.")
                    else:
                        missing = missing_channels(latest_values, channels) or stale_channels(latest_times, channels, args.stale_seconds, now)
                        if missing:
                            missing_text = ", ".join(f"{c.pin}/{c.motor}" for c in missing)
                            print("Cannot enable; waiting for:", missing_text)
                        else:
                            enable_errors: dict[str, float] = {}
                            if robot is not None:
                                current_positions = obs_to_positions(robot.get_observation(), channels)
                                if args.control_mode == "relative":
                                    center_targets = current_positions
                                else:
                                    enable_errors = excessive_enable_errors(
                                        current_positions,
                                        latest_targets,
                                        args.max_enable_error,
                                    )
                            elif args.control_mode == "relative":
                                center_targets = dict(latest_targets) or dry_run_positions(channels)

                            if enable_errors:
                                errors_text = " | ".join(
                                    f"{motor}:{error:+.2f}" for motor, error in enable_errors.items()
                                )
                                print(
                                    "Cannot enable absolute control; align leader and robot first. "
                                    f"Target errors: {errors_text}"
                                )
                            else:
                                if robot is not None:
                                    robot.bus.enable_torque()
                                if args.control_mode == "relative":
                                    center_pots = {pin: latest_values[pin] for pin in pins}
                                last_sent_targets = {}
                                if args.control_mode == "relative":
                                    latest_targets = {}  # Recompute from the new centre on the next input sample.
                                enabled = True
                                print("Streaming ENABLED.")
                                print("Center pots:", format_values(center_pots, pins))
                                print("Center targets:", format_values(center_targets, motors))
                elif key == "c":
                    missing = missing_channels(latest_values, channels)
                    if missing:
                        missing_text = ", ".join(f"{c.pin}/{c.motor}" for c in missing)
                        print("Cannot recenter; waiting for:", missing_text)
                    else:
                        if robot is not None:
                            center_targets = obs_to_positions(robot.get_observation(), channels)
                        elif latest_targets:
                            center_targets = dict(latest_targets)
                        center_pots = {pin: latest_values[pin] for pin in pins}
                        last_sent_targets = {}
                        latest_targets = {}
                        print("Recentered.")
                        print("Center pots:", format_values(center_pots, pins))
                        print("Center targets:", format_values(center_targets, motors))
                elif key == "t":
                    enabled = False
                    if robot is not None:
                        robot.bus.disable_torque()
                    print("Torque disabled. Streaming paused.")
                elif key == "p":
                    print("Pots:", format_values(latest_values, pins))
                    print("Targets:", format_targets(latest_targets, channels))

                if enabled:
                    stale = stale_channels(latest_times, channels, args.stale_seconds, now)
                    if stale:
                        enabled = False
                        if robot is not None:
                            robot.bus.disable_torque()
                        print(
                            "\nSerial data stale for "
                            + ", ".join(f"{channel.pin}/{channel.motor}" for channel in stale)
                            + ". Torque disabled."
                        )

                should_send = (
                    enabled
                    and latest_targets
                    and len(latest_targets) == len(channels)
                    and (now - last_send_t) >= interval
                    and any(
                        motor not in last_sent_targets
                        or math.fabs(latest_targets[motor] - last_sent_targets[motor]) >= args.deadband
                        for motor in motors
                    )
                )
                if should_send:
                    if robot is not None:
                        sent = robot.send_action(action_from_targets(latest_targets))
                        last_sent_targets = {
                            key.removesuffix(".pos"): float(value) for key, value in sent.items()
                        }
                    else:
                        last_sent_targets = dict(latest_targets)
                    last_send_t = now

                should_publish_rviz = (
                    rviz_socket is not None
                    and rviz_target is not None
                    and enabled
                    and len(latest_targets) == len(channels)
                    and (now - last_rviz_t) >= interval
                )
                if should_publish_rviz:
                    rviz_targets = (
                        last_sent_targets
                        if robot is not None and len(last_sent_targets) == len(channels)
                        else latest_targets
                    )
                    payload = rviz_payload(rviz_sequence, rviz_targets, joint_limits)
                    rviz_socket.sendto(json.dumps(payload).encode("utf-8"), rviz_target)
                    rviz_sequence += 1
                    last_rviz_t = now

                if telemetry is not None:
                    telemetry.update(robot, targets=latest_targets, enabled=enabled,
                                     input_fresh=not (missing_channels(latest_values, channels) or stale_channels(latest_times, channels, args.stale_seconds, now)))

                if now - last_print_t >= 0.25:
                    state = "ENABLED" if enabled else "PAUSED"
                    missing = missing_channels(latest_values, channels)
                    if missing:
                        missing_text = ",".join(f"{channel.pin}/{channel.motor}" for channel in missing)
                        message = "waiting for " + missing_text
                    else:
                        message = format_targets(latest_targets, channels)
                    print(f"\r{state} | {message}", end="", flush=True)
                    last_print_t = now

                time.sleep(0.002)
        finally:
            print()
            if robot is not None:
                robot.disconnect()
                print("Disconnected. Torque disabled.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
