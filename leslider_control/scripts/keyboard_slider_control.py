#!/usr/bin/env python
r"""Fail-closed 0..100 position tester for only LeSlider servo ID 7.

Run from the LeRobot repository root::

    .\control.ps1 slider -RobotPort COM6

The STS3215 does not retain its multi-turn revolution count across power
cycles. Consequently this GUI requires one manual endpoint reference every
time it opens. Motion is issued as small checkpoints and monitored for wrong
direction, overshoot, stalls, and departure from the session's physical range.
Any failed check disables ID-7 torque.
"""

from __future__ import annotations

import argparse
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from so101_slider_follower import (
    MIN_SLIDER_TRAVEL_TICKS,
    SLIDER,
    SLIDER_ENCODER_MAX,
    SLIDER_ENCODER_MIN,
    SLIDER_SAFE_PHASE,
    prepare_slider_position_mode,
    require_slider_safety_metadata,
    slider_calibration_path,
    validate_slider_id,
)

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode

if TYPE_CHECKING:
    import tkinter as tk

MAX_SAFE_VELOCITY = 600
DEFAULT_TORQUE_LIMIT = 300
POSITION_P_COEFFICIENT = 32
MINIMUM_STARTUP_FORCE = 80
MOTION_ACCELERATION = 254

# The slider transmission has enough static friction/backlash that a fixed
# 40-tick probe can produce no encoder motion even though the servo is healthy.
# Start small, then take up the mechanism in bounded steps. The probe is always
# capped by the requested target, so it can never command past that target.
PROBE_TICKS = 80
PROBE_STEP_TICKS = 80
MAX_PROBE_TICKS = 320
PROBE_SUCCESS_TICKS = 8
WRONG_WAY_TICKS = 12
WRONG_WAY_HARD_TICKS = 48
WRONG_WAY_CONFIRM_SECONDS = 0.12
SETTLE_SECONDS = 0.25
SETTLE_MAX_DRIFT_TICKS = 32
CHECKPOINT_TICKS = 100
CHECKPOINT_NEAR_TICKS = 30
TARGET_TOLERANCE_TICKS = 15
OVERSHOOT_TICKS = 20
PROBE_STEP_SECONDS = 0.35
PROBE_TIMEOUT_SECONDS = 1.75
STALL_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class SliderEndpoints:
    slider_id: int
    raw_min: int
    raw_max: int
    reversed: bool

    @property
    def raw_at_zero(self) -> int:
        return self.raw_max if self.reversed else self.raw_min

    @property
    def raw_at_hundred(self) -> int:
        return self.raw_min if self.reversed else self.raw_max

    @property
    def travel_ticks(self) -> int:
        return self.raw_max - self.raw_min

    @property
    def direction_to_hundred(self) -> int:
        return -1 if self.reversed else 1


@dataclass(frozen=True)
class SliderStatus:
    encoder_position: int
    raw_position: int
    normalized_position: float | None
    raw_goal: int
    torque_enabled: bool
    moving: bool


@dataclass(frozen=True)
class MotionEvent:
    kind: Literal["success", "fault"]
    message: str


@dataclass
class MotionState:
    requested_position: float
    target_raw: int
    start_raw: int
    direction: int
    checkpoint_raw: int
    best_error: int
    started_at: float
    last_progress_at: float
    probe_distance: int
    last_probe_at: float
    wrong_way_since: float | None = None
    stage: Literal["settling", "probe", "running"] = "settling"


def endpoints_from_calibration(calibration: MotorCalibration) -> SliderEndpoints:
    if calibration.range_min >= calibration.range_max:
        raise ValueError(
            f"Invalid slider limits {calibration.range_min}..{calibration.range_max}; recalibrate ID 7."
        )
    if calibration.range_max - calibration.range_min < MIN_SLIDER_TRAVEL_TICKS:
        raise ValueError(
            f"Saved slider travel is only {calibration.range_max - calibration.range_min} ticks "
            f"(minimum {MIN_SLIDER_TRAVEL_TICKS}). Run "
            "./control.ps1 calibrate-slider again."
        )
    return SliderEndpoints(
        slider_id=calibration.id,
        raw_min=calibration.range_min,
        raw_max=calibration.range_max,
        reversed=bool(calibration.drive_mode),
    )


def rebase_endpoints(
    calibrated: SliderEndpoints,
    current_raw: int,
    reference_position: Literal[0, 100],
) -> SliderEndpoints:
    """Create this powered session's absolute range from one physical endpoint."""
    direction = calibrated.direction_to_hundred
    if reference_position == 0:
        raw_zero = current_raw
        raw_hundred = current_raw + direction * calibrated.travel_ticks
    elif reference_position == 100:
        raw_hundred = current_raw
        raw_zero = current_raw - direction * calibrated.travel_ticks
    else:  # pragma: no cover - Literal plus runtime guard for external callers
        raise ValueError("Reference position must be 0 or 100.")

    raw_min = min(raw_zero, raw_hundred)
    raw_max = max(raw_zero, raw_hundred)
    if raw_min < SLIDER_ENCODER_MIN or raw_max > SLIDER_ENCODER_MAX:
        raise ValueError(
            f"Session range {raw_min}..{raw_max} exceeds the servo's supported "
            f"{SLIDER_ENCODER_MIN}..{SLIDER_ENCODER_MAX} multi-turn counter. "
            "Power-cycle with torque off, place the carriage at an endpoint, and reopen the GUI."
        )
    return SliderEndpoints(
        slider_id=calibrated.slider_id,
        raw_min=raw_min,
        raw_max=raw_max,
        reversed=raw_zero > raw_hundred,
    )


def normalized_to_raw(position: float, endpoints: SliderEndpoints) -> int:
    if not 0.0 <= position <= 100.0:
        raise ValueError("Position must be from 0 through 100.")
    fraction = position / 100.0
    if endpoints.reversed:
        fraction = 1.0 - fraction
    return round(endpoints.raw_min + fraction * endpoints.travel_ticks)


def raw_to_normalized(raw_position: int, endpoints: SliderEndpoints) -> float:
    fraction = (raw_position - endpoints.raw_min) / endpoints.travel_ticks
    if endpoints.reversed:
        fraction = 1.0 - fraction
    return fraction * 100.0


def load_slider_calibration(path: Path, expected_id: int) -> MotorCalibration:
    if not path.is_file():
        raise FileNotFoundError(
            f"Slider calibration not found: {path}\n"
            "Run ./control.ps1 calibrate-slider first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if SLIDER not in payload:
        raise ValueError(f"Calibration {path} has no {SLIDER!r} entry.")
    calibration = MotorCalibration(**payload[SLIDER])
    if calibration.id != expected_id:
        raise ValueError(f"Calibration uses slider ID {calibration.id}, but --slider-id is {expected_id}.")
    endpoints_from_calibration(calibration)
    require_slider_safety_metadata(path, calibration)
    return calibration


class SliderPositionController:
    """ID-7 controller that never sends an unchecked full-distance target."""

    def __init__(
        self,
        port: str,
        calibrated_endpoints: SliderEndpoints,
        torque_limit: int = DEFAULT_TORQUE_LIMIT,
    ):
        self.calibrated_endpoints = calibrated_endpoints
        self.session_endpoints: SliderEndpoints | None = None
        self.torque_limit = torque_limit
        self.motion: MotionState | None = None
        self.bus = FeetechMotorsBus(
            port=port,
            motors={SLIDER: Motor(calibrated_endpoints.slider_id, "sts3215", MotorNormMode.RANGE_0_100)},
        )
        self.connected = False

    def _configure_drive_response(self) -> None:
        """Apply and verify settings that can overcome the slider's static friction."""
        expected = {
            "Torque_Limit": self.torque_limit,
            "P_Coefficient": POSITION_P_COEFFICIENT,
            "Minimum_Startup_Force": MINIMUM_STARTUP_FORCE,
        }
        self.bus.disable_torque([SLIDER])
        for register, value in expected.items():
            current = int(self.bus.read(register, SLIDER, normalize=False))
            if current != value:
                self.bus.write(register, SLIDER, value, normalize=False)
        self.bus.disable_torque([SLIDER])

        mismatches = {}
        for register, expected_value in expected.items():
            actual = int(self.bus.read(register, SLIDER, normalize=False))
            if actual != expected_value:
                mismatches[register] = (expected_value, actual)
        if mismatches:
            details = ", ".join(
                f"{register} expected {expected_value}, read {actual}"
                for register, (expected_value, actual) in mismatches.items()
            )
            raise RuntimeError(f"ID 7 drive settings did not stick: {details}.")

    def connect(self) -> SliderStatus:
        self.bus.connect()
        try:
            prepare_slider_position_mode(self.bus)
            self._configure_drive_response()
            self.bus.disable_torque([SLIDER])
            torque = int(self.bus.read("Torque_Enable", SLIDER, normalize=False))
            mode = int(self.bus.read("Operating_Mode", SLIDER, normalize=False))
            phase = int(self.bus.read("Phase", SLIDER, normalize=False))
            if torque != 0 or mode != OperatingMode.POSITION.value or phase != SLIDER_SAFE_PHASE:
                raise RuntimeError(
                    "ID 7 setup verification failed: "
                    f"Torque_Enable={torque}, Operating_Mode={mode}, Phase={phase}."
                )
            self.connected = True
            return self.read_status()
        except Exception:
            self.close()
            raise

    def set_reference(self, reference_position: Literal[0, 100]) -> SliderEndpoints:
        if not self.connected:
            raise RuntimeError("Slider is not connected.")
        self.stop()
        current = self.read_status().raw_position
        self.session_endpoints = rebase_endpoints(
            self.calibrated_endpoints,
            current,
            reference_position,
        )
        return self.session_endpoints

    def move_to(self, position: float, goal_velocity: int) -> int:
        if not self.connected:
            raise RuntimeError("Slider is not connected.")
        if self.session_endpoints is None:
            raise RuntimeError("Set the current physical endpoint as 0 or 100 before pressing Go.")
        if self.motion is not None:
            raise RuntimeError("A slider move is already active. Press Stop first.")
        if not 1 <= goal_velocity <= MAX_SAFE_VELOCITY:
            raise ValueError(f"Speed must be from 1 through {MAX_SAFE_VELOCITY} in the limit tester.")

        status = self.read_status()
        start = status.raw_position
        endpoints = self.session_endpoints
        if not endpoints.raw_min <= start <= endpoints.raw_max:
            self.stop()
            raise RuntimeError(
                f"Current raw position {start} is outside this session's physical range "
                f"{endpoints.raw_min}..{endpoints.raw_max}. Reposition by hand with torque off and set "
                "the endpoint reference again."
            )

        target = normalized_to_raw(position, endpoints)
        difference = target - start
        if abs(difference) <= TARGET_TOLERANCE_TICKS:
            self.stop()
            return target
        direction = 1 if difference > 0 else -1

        # Prime all motion parameters against the current position, then make
        # torque-off the final setup write. This prevents any stale goal chase.
        self.bus.disable_torque([SLIDER])
        self.bus.write("Torque_Limit", SLIDER, self.torque_limit, normalize=False)
        self.bus.write("Acceleration", SLIDER, MOTION_ACCELERATION, normalize=False)
        # Feetech's official WritePosEx command clears these two bytes on every
        # position command. A stale nonzero time-profile value can otherwise
        # change how the next checkpoint is interpreted.
        self.bus.write("Goal_Time", SLIDER, 0, normalize=False)
        self.bus.write("Goal_Position", SLIDER, start, normalize=False)
        self.bus.write("Goal_Velocity", SLIDER, goal_velocity, normalize=False)
        self.bus.disable_torque([SLIDER])

        now = time.monotonic()
        self.motion = MotionState(
            requested_position=position,
            target_raw=target,
            start_raw=start,
            direction=direction,
            checkpoint_raw=start,
            best_error=abs(difference),
            started_at=now,
            last_progress_at=now,
            probe_distance=0,
            last_probe_at=now,
        )
        try:
            # Engage torque while commanding the exact measured position. This
            # lets one-sided belt tension settle before a direction is judged.
            self.bus.enable_torque([SLIDER])
        except Exception:
            self.motion = None
            self.bus.disable_torque([SLIDER])
            raise
        return target

    def _next_checkpoint(self, current: int, motion: MotionState) -> int:
        remaining = abs(motion.target_raw - current)
        return current + motion.direction * min(CHECKPOINT_TICKS, remaining)

    @staticmethod
    def _wrong_way_is_confirmed(motion: MotionState, amount: int, now: float) -> bool:
        """Reject sustained reverse travel without tripping on one feedback kick."""
        if amount < WRONG_WAY_TICKS:
            motion.wrong_way_since = None
            return False
        if amount >= WRONG_WAY_HARD_TICKS:
            return True
        if motion.wrong_way_since is None:
            motion.wrong_way_since = now
            return False
        return now - motion.wrong_way_since >= WRONG_WAY_CONFIRM_SECONDS

    def _finish_motion(self, kind: Literal["success", "fault"], message: str) -> MotionEvent:
        self.bus.disable_torque([SLIDER])
        self.motion = None
        return MotionEvent(kind=kind, message=message)

    def monitor_motion(self) -> tuple[SliderStatus, MotionEvent | None]:
        status = self.read_status()
        motion = self.motion
        if motion is None:
            return status, None
        endpoints = self.session_endpoints
        if endpoints is None:  # defensive; cannot occur through move_to
            return status, self._finish_motion("fault", "Session endpoint reference was lost.")

        current = status.raw_position
        if not endpoints.raw_min <= current <= endpoints.raw_max:
            event = self._finish_motion(
                "fault",
                f"HARD STOP: raw {current} left allowed range {endpoints.raw_min}..{endpoints.raw_max}.",
            )
            return status, event
        if not status.torque_enabled:
            self.motion = None
            return status, MotionEvent("fault", "Servo torque switched off unexpectedly during motion.")

        now = time.monotonic()
        if motion.stage == "settling":
            drift = current - motion.start_raw
            if abs(drift) >= SETTLE_MAX_DRIFT_TICKS:
                event = self._finish_motion(
                    "fault",
                    f"HOLD DRIFT: ID 7 moved {drift:+d} ticks while holding its starting position; "
                    "torque was turned off before a direction command was sent.",
                )
                return status, event
            if now - motion.started_at < SETTLE_SECONDS:
                return status, None

            difference = motion.target_raw - current
            if abs(difference) <= TARGET_TOLERANCE_TICKS:
                event = self._finish_motion(
                    "success",
                    f"Reached {motion.requested_position:.2f} at raw {current}; torque automatically turned off.",
                )
                return status, event
            motion.start_raw = current
            motion.direction = 1 if difference > 0 else -1
            motion.best_error = abs(difference)
            motion.started_at = now
            motion.last_progress_at = now
            motion.last_probe_at = now
            motion.probe_distance = min(PROBE_TICKS, abs(difference))
            motion.checkpoint_raw = current + motion.direction * motion.probe_distance
            motion.stage = "probe"
            self.bus.write("Goal_Position", SLIDER, motion.checkpoint_raw, normalize=False)
            return status, None

        progress = motion.direction * (current - motion.start_raw)
        error = abs(motion.target_raw - current)
        reverse_progress = max(0, -progress)
        if self._wrong_way_is_confirmed(motion, reverse_progress, now):
            event = self._finish_motion(
                "fault",
                f"WRONG DIRECTION: started {motion.start_raw}, requested {motion.target_raw}, "
                f"reached {current} ({reverse_progress} ticks reverse).",
            )
            return status, event
        if reverse_progress >= WRONG_WAY_TICKS:
            return status, None
        if motion.direction * (current - motion.target_raw) >= OVERSHOOT_TICKS:
            event = self._finish_motion(
                "fault",
                f"OVERSHOOT: target {motion.target_raw}, reached {current}.",
            )
            return status, event

        if motion.stage == "probe":
            if progress >= min(PROBE_SUCCESS_TICKS, abs(motion.target_raw - motion.start_raw)):
                motion.stage = "running"
                motion.best_error = error
                motion.last_progress_at = now
                checkpoint = self._next_checkpoint(current, motion)
                motion.checkpoint_raw = checkpoint
                self.bus.write("Goal_Position", SLIDER, checkpoint, normalize=False)
            elif (
                now - motion.last_probe_at >= PROBE_STEP_SECONDS
                and motion.probe_distance < min(MAX_PROBE_TICKS, abs(motion.target_raw - motion.start_raw))
            ):
                probe_limit = min(MAX_PROBE_TICKS, abs(motion.target_raw - motion.start_raw))
                motion.probe_distance = min(probe_limit, motion.probe_distance + PROBE_STEP_TICKS)
                motion.checkpoint_raw = motion.start_raw + motion.direction * motion.probe_distance
                self.bus.write("Goal_Position", SLIDER, motion.checkpoint_raw, normalize=False)
                motion.last_probe_at = now
            elif now - motion.started_at >= PROBE_TIMEOUT_SECONDS:
                event = self._finish_motion(
                    "fault",
                    "NO MOVEMENT: ID 7 did not take up the slider after a bounded "
                    f"{motion.probe_distance}-tick probe; observed progress was {max(0, progress)} ticks. "
                    "Torque was turned off.",
                )
                return status, event
            return status, None

        if error <= TARGET_TOLERANCE_TICKS:
            event = self._finish_motion(
                "success",
                f"Reached {motion.requested_position:.2f} at raw {current}; torque automatically turned off.",
            )
            return status, event

        if error < motion.best_error - 2:
            motion.best_error = error
            motion.last_progress_at = now
            motion.wrong_way_since = None
        elif self._wrong_way_is_confirmed(motion, error - motion.best_error, now):
            event = self._finish_motion(
                "fault",
                f"WRONG DIRECTION: distance to target increased by {error - motion.best_error} ticks "
                f"at raw {current}.",
            )
            return status, event
        elif now - motion.last_progress_at >= STALL_TIMEOUT_SECONDS:
            event = self._finish_motion(
                "fault",
                f"STALL: no progress toward raw target {motion.target_raw}; torque turned off.",
            )
            return status, event

        if abs(motion.checkpoint_raw - current) <= CHECKPOINT_NEAR_TICKS:
            checkpoint = self._next_checkpoint(current, motion)
            motion.checkpoint_raw = checkpoint
            self.bus.write("Goal_Position", SLIDER, checkpoint, normalize=False)
        return status, None

    def stop(self) -> None:
        self.motion = None
        if self.bus.is_connected:
            self.bus.disable_torque([SLIDER])

    def read_status(self) -> SliderStatus:
        raw_position = int(self.bus.read("Present_Position", SLIDER, normalize=False))
        encoder_position = raw_position % 4096
        normalized = (
            raw_to_normalized(raw_position, self.session_endpoints)
            if self.session_endpoints is not None
            else None
        )
        return SliderStatus(
            encoder_position=encoder_position,
            raw_position=raw_position,
            normalized_position=normalized,
            raw_goal=int(self.bus.read("Goal_Position", SLIDER, normalize=False)),
            torque_enabled=bool(int(self.bus.read("Torque_Enable", SLIDER, normalize=False))),
            moving=bool(int(self.bus.read("Moving", SLIDER, normalize=False))),
        )

    def close(self) -> None:
        if self.bus.is_connected:
            with suppress(Exception):
                self.stop()
            with suppress(Exception):
                self.bus.disconnect(disable_torque=True)
        self.connected = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the fail-closed ID-7 slider position tester.")
    parser.add_argument("--port", default="COM6", help="Follower bus. Default: COM6.")
    parser.add_argument("--robot-id", default="my_so101_follower")
    parser.add_argument("--slider-id", type=int, default=7)
    parser.add_argument("--calibration-file", type=Path)
    parser.add_argument("--goal-velocity", type=int, default=300, help="Initial safe speed (1..600).")
    parser.add_argument(
        "--torque-limit",
        type=int,
        default=DEFAULT_TORQUE_LIMIT,
        help=f"ID-7 torque limit (50..500). Default: {DEFAULT_TORQUE_LIMIT}.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    validate_slider_id(args.slider_id)
    if not 1 <= args.goal_velocity <= MAX_SAFE_VELOCITY:
        raise ValueError(f"--goal-velocity must be from 1 through {MAX_SAFE_VELOCITY}.")
    if not 50 <= args.torque_limit <= 500:
        raise ValueError("--torque-limit must be from 50 through 500.")


class SliderPositionApp:
    def __init__(self, root: tk.Tk, controller: SliderPositionController, initial_speed: int):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.controller = controller
        self.closed = False
        self.position_var = tk.StringVar(value="50")
        self.speed_var = tk.StringVar(value=str(initial_speed))
        self.connection_var = tk.StringVar(value="Connecting to ID 7 with torque off...")
        self.reference_var = tk.StringVar(
            value=(
                "After calibration, power-cycle the follower with torque off. Then move the carriage slowly "
                "to a physical endpoint and set its reference."
            )
        )
        self.position_status_var = tk.StringVar(value="Current raw position: --")
        self.target_status_var = tk.StringVar(value="No target sent")
        self.torque_status_var = tk.StringVar(value="TORQUE OFF")

        root.title("LeSlider Safe Position Tester - ID 7")
        root.geometry("680x545")
        root.minsize(620, 510)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda _event: self.stop())
        root.bind("<Return>", lambda _event: self.go())

        style = ttk.Style(root)
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 11))
        style.configure("Stop.TButton", font=("Segoe UI", 12, "bold"), padding=10)
        style.configure("Go.TButton", font=("Segoe UI", 12, "bold"), padding=10)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Fail-Closed Slider Position Tester", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=self.connection_var).pack(anchor="w", pady=(3, 10))

        calibrated = controller.calibrated_endpoints
        ttk.Label(
            frame,
            text=(
                f"Calibrated travel: {calibrated.travel_ticks} ticks "
                f"({calibrated.travel_ticks / 4096:.2f} turns). Signed multi-turn feedback is active."
            ),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, textvariable=self.reference_var, wraplength=630).pack(anchor="w", pady=(0, 8))

        reference_row = ttk.Frame(frame)
        reference_row.pack(fill="x", pady=(0, 14))
        self.reference_zero_button = ttk.Button(
            reference_row,
            text="SET CURRENT PHYSICAL END AS 0",
            command=lambda: self.set_reference(0),
            state="disabled",
        )
        self.reference_zero_button.pack(side="left")
        self.reference_hundred_button = ttk.Button(
            reference_row,
            text="SET CURRENT PHYSICAL END AS 100",
            command=lambda: self.set_reference(100),
            state="disabled",
        )
        self.reference_hundred_button.pack(side="left", padx=(10, 0))

        entry_row = ttk.Frame(frame)
        entry_row.pack(fill="x")
        ttk.Label(entry_row, text="Target position (0-100):").grid(row=0, column=0, sticky="w")
        self.position_entry = ttk.Entry(entry_row, textvariable=self.position_var, width=12, font=("Segoe UI", 14))
        self.position_entry.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(entry_row, text="Safe speed (1-600):").grid(row=0, column=1, sticky="w", padx=(24, 0))
        ttk.Entry(entry_row, textvariable=self.speed_var, width=12, font=("Segoe UI", 14)).grid(
            row=1, column=1, sticky="w", padx=(24, 0), pady=(4, 0)
        )
        self.go_button = ttk.Button(entry_row, text="GO", style="Go.TButton", command=self.go, state="disabled")
        self.go_button.grid(row=1, column=2, padx=(24, 0), pady=(4, 0))

        preset_row = ttk.Frame(frame)
        preset_row.pack(fill="x", pady=(14, 12))
        ttk.Label(preset_row, text="Set value:").pack(side="left")
        for value in (0, 25, 50, 75, 100):
            ttk.Button(
                preset_row,
                text=str(value),
                command=lambda selected=value: self.position_var.set(str(selected)),
                width=5,
            ).pack(side="left", padx=(8, 0))

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, textvariable=self.position_status_var, style="Status.TLabel").pack(anchor="w", pady=(8, 2))
        ttk.Label(frame, textvariable=self.target_status_var, style="Status.TLabel", wraplength=630).pack(
            anchor="w", pady=2
        )
        ttk.Label(frame, textvariable=self.torque_status_var, style="Status.TLabel").pack(anchor="w", pady=2)
        self.stop_button = ttk.Button(
            frame,
            text="STOP / TORQUE OFF  (Esc)",
            style="Stop.TButton",
            command=self.stop,
            state="disabled",
        )
        self.stop_button.pack(fill="x", pady=(14, 0))

        self.position_entry.focus_set()
        root.after(100, self.connect)

    def connect(self) -> None:
        from tkinter import messagebox

        try:
            status = self.controller.connect()
        except Exception as exc:
            self.connection_var.set("Connection failed; torque-off cleanup attempted.")
            messagebox.showerror("Slider connection failed", str(exc), parent=self.root)
            return
        self.connection_var.set(
            f"Connected on {self.controller.bus.port}; Phase verified, torque OFF, "
            f"drive response={self.controller.torque_limit}/{POSITION_P_COEFFICIENT}/{MINIMUM_STARTUP_FORCE}."
        )
        self.reference_zero_button.configure(state="normal")
        self.reference_hundred_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self.update_status_labels(status)
        self.root.after(100, self.refresh_status)

    def set_reference(self, position: Literal[0, 100]) -> None:
        from tkinter import messagebox

        try:
            endpoints = self.controller.set_reference(position)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot set endpoint reference", str(exc), parent=self.root)
            return
        self.reference_var.set(
            f"Session reference set: 0 = raw {endpoints.raw_at_zero}; "
            f"100 = raw {endpoints.raw_at_hundred}. GO is now enabled."
        )
        self.go_button.configure(state="normal")
        self.target_status_var.set("Endpoint reference accepted; no movement was commanded.")

    def go(self) -> None:
        from tkinter import messagebox

        try:
            position = float(self.position_var.get().strip())
            speed = int(self.speed_var.get().strip())
            target = self.controller.move_to(position, speed)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Slider command refused", str(exc), parent=self.root)
            return
        self.go_button.configure(state="disabled")
        self.target_status_var.set(
            f"Requested {position:.2f} -> raw {target}. Holding current position, then running bounded take-up."
        )
        self.torque_status_var.set("TORQUE ON - guarded settling/take-up; Esc stops immediately")

    def stop(self) -> None:
        self.controller.stop()
        if self.controller.session_endpoints is not None:
            self.go_button.configure(state="normal")
        self.target_status_var.set("Stopped by user; target sequence cancelled.")
        self.torque_status_var.set("TORQUE OFF - stopped")

    def refresh_status(self) -> None:
        from tkinter import messagebox

        if self.closed or not self.controller.connected:
            return
        try:
            status, event = self.controller.monitor_motion()
            self.update_status_labels(status)
        except Exception as exc:
            self.connection_var.set(f"Communication failed: {exc}")
            with suppress(Exception):
                self.controller.stop()
            self.torque_status_var.set("TORQUE-OFF cleanup attempted after communication error")
            messagebox.showerror(
                "Slider communication failure",
                f"{exc}\n\nCut follower power if the carriage is still moving.",
                parent=self.root,
            )
            return

        if event is not None:
            self.go_button.configure(state="normal")
            self.target_status_var.set(f"{event.kind.upper()}: {event.message}")
            self.torque_status_var.set("TORQUE OFF - automatic stop")
            if event.kind == "fault":
                messagebox.showerror("Slider safety stop", event.message, parent=self.root)

        delay_ms = 20 if self.controller.motion is not None else 100
        self.root.after(delay_ms, self.refresh_status)

    def update_status_labels(self, status: SliderStatus) -> None:
        if status.normalized_position is None:
            self.position_status_var.set(
                f"Encoder: {status.encoder_position}   |   live unwrapped: {status.raw_position} "
                "(set an endpoint reference)"
            )
        else:
            endpoints = self.controller.session_endpoints
            outside = endpoints is not None and not endpoints.raw_min <= status.raw_position <= endpoints.raw_max
            suffix = "  [OUTSIDE SESSION LIMITS]" if outside else ""
            self.position_status_var.set(
                f"Current: {status.normalized_position:.2f}   |   encoder {status.encoder_position}   |   "
                f"unwrapped {status.raw_position}{suffix}"
            )
        if status.torque_enabled:
            self.torque_status_var.set("TORQUE ON - guarded motion")
        elif self.controller.motion is None:
            self.torque_status_var.set("TORQUE OFF - stopped")

    def close(self) -> None:
        self.closed = True
        self.controller.close()
        self.root.destroy()


def main() -> int:
    import tkinter as tk

    args = parse_args()
    validate_args(args)
    calibration_path = args.calibration_file or slider_calibration_path(args.robot_id)
    calibration = load_slider_calibration(calibration_path, args.slider_id)
    calibrated_endpoints = endpoints_from_calibration(calibration)
    controller = SliderPositionController(args.port, calibrated_endpoints, args.torque_limit)

    root = tk.Tk()
    SliderPositionApp(root, controller, args.goal_velocity)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
