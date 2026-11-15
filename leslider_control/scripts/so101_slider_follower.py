#!/usr/bin/env python
"""SO-101 follower with one extended-position servo added as ``slider``.

This adapter deliberately builds on LeRobot's working :class:`SOFollower`
implementation. Motor IDs 1..6 keep the stock SO-101 behavior and calibration;
motor ID 7 is an STS3215 in multi-turn position mode so the rack-and-pinion
slider can span several output-shaft revolutions.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - this local hardware utility targets Windows
    msvcrt = None

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.config import RobotConfig
from lerobot.robots.so_follower import SOFollower
from lerobot.robots.so_follower.config_so_follower import SOFollowerConfig
from lerobot.utils.constants import HF_LEROBOT_CALIBRATION, ROBOTS

logger = logging.getLogger(__name__)

SLIDER = "slider"
ARM_MOTORS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
ALL_MOTORS = (*ARM_MOTORS, SLIDER)
SLIDER_ENCODER_MIN = -30719
SLIDER_ENCODER_MAX = 30719
# Require at least one complete motor revolution. The measured physical rail
# travel on this rig is 6,554 ticks (1.600 turns).
MIN_SLIDER_TRAVEL_TICKS = 4096
MULTITURN_PHASE_BIT = 0x10
SLIDER_SAFE_PHASE = 28
SLIDER_SAFETY_VERSION = 4
SLIDER_RAW_LATCH_TOLERANCE_TICKS = 32
SLIDER_SESSION_RANGE_TOLERANCE_TICKS = 32
STOCK_ARM_ROBOT_NAMES = ("so_follower", "so101_follower")


@dataclass
class SO101SliderFollowerConfig(RobotConfig, SOFollowerConfig):
    """Configuration for the stock SO-101 follower plus position servo ID 7."""

    slider_id: int = 7
    import_arm_calibration: bool = True


@dataclass
class UnwrappedPositionTracker:
    """Turn a continuously sampled 12-bit encoder stream into cumulative ticks."""

    last_encoder: int
    unwrapped: int
    wraps: int = 0

    @classmethod
    def from_encoder(cls, encoder: int) -> UnwrappedPositionTracker:
        return cls(last_encoder=encoder, unwrapped=encoder)

    def update(self, encoder: int) -> int:
        delta = encoder - self.last_encoder
        if delta > 2048:
            delta -= 4096
            self.wraps -= 1
        elif delta < -2048:
            delta += 4096
            self.wraps += 1
        self.unwrapped += delta
        self.last_encoder = encoder
        return self.unwrapped


def validate_slider_id(slider_id: int) -> None:
    if slider_id in range(1, 7):
        raise ValueError(f"slider_id={slider_id} collides with SO-101 arm motor IDs 1..6.")
    if not 0 <= slider_id <= 253:
        raise ValueError("slider_id must be between 0 and 253.")


def stock_arm_calibration_candidates(robot_id: str | None) -> tuple[Path, ...]:
    return tuple(
        HF_LEROBOT_CALIBRATION / ROBOTS / robot_name / f"{robot_id}.json"
        for robot_name in STOCK_ARM_ROBOT_NAMES
    )


def stock_arm_calibration_path(robot_id: str | None) -> Path:
    candidates = stock_arm_calibration_candidates(robot_id)
    return next((path for path in candidates if path.is_file()), candidates[0])


def load_arm_calibration(path: Path) -> dict[str, MotorCalibration]:
    """Read the six existing arm entries without connecting to the motor bus."""
    if not path.is_file():
        raise FileNotFoundError(f"SO-101 arm calibration not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = [motor for motor in ARM_MOTORS if motor not in payload]
    if missing:
        raise ValueError(f"Arm calibration {path} is missing: {', '.join(missing)}")
    calibration = {motor: MotorCalibration(**payload[motor]) for motor in ARM_MOTORS}
    wrong_ids = {
        motor: value.id
        for expected_id, (motor, value) in enumerate(calibration.items(), start=1)
        if value.id != expected_id
    }
    if wrong_ids:
        details = ", ".join(f"{motor}=ID {id_}" for motor, id_ in wrong_ids.items())
        raise ValueError(f"Unexpected motor IDs in {path}: {details}")
    return calibration


def slider_calibration_path(robot_id: str | None) -> Path:
    return HF_LEROBOT_CALIBRATION / ROBOTS / SO101SliderFollower.name / f"{robot_id}.json"


def slider_safety_metadata_path(calibration_path: Path) -> Path:
    return calibration_path.with_name(f"{calibration_path.stem}.slider_safety.json")


def save_slider_safety_metadata(path: Path, calibration: MotorCalibration) -> Path:
    metadata_path = slider_safety_metadata_path(path)
    payload = {
        "version": SLIDER_SAFETY_VERSION,
        "phase": SLIDER_SAFE_PHASE,
        "slider_id": calibration.id,
        "drive_mode": calibration.drive_mode,
        "range_min": calibration.range_min,
        "range_max": calibration.range_max,
        "travel_ticks": calibration.range_max - calibration.range_min,
    }
    temporary = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


def slider_safety_metadata_matches(path: Path, calibration: MotorCalibration) -> bool:
    metadata_path = slider_safety_metadata_path(path)
    if not metadata_path.is_file():
        return False
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = {
        "version": SLIDER_SAFETY_VERSION,
        "phase": SLIDER_SAFE_PHASE,
        "slider_id": calibration.id,
        "drive_mode": calibration.drive_mode,
        "range_min": calibration.range_min,
        "range_max": calibration.range_max,
        "travel_ticks": calibration.range_max - calibration.range_min,
    }
    return all(payload.get(key) == value for key, value in expected.items())


def require_slider_safety_metadata(path: Path, calibration: MotorCalibration) -> None:
    if not slider_safety_metadata_matches(path, calibration):
        raise RuntimeError(
            "This slider calibration predates the Phase-28 signed multi-turn feedback fix. "
            "It is intentionally blocked. Run ./control.ps1 calibrate-slider once more."
        )


def save_combined_slider_calibration(
    robot_id: str | None,
    arm_calibration: dict[str, MotorCalibration],
    slider_calibration: MotorCalibration,
    output_path: Path | None = None,
) -> Path:
    """Save the six arm entries byte-for-value-equivalent plus the new ID 7 entry."""
    path = output_path or slider_calibration_path(robot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    combined = {
        **{motor: arm_calibration[motor] for motor in ARM_MOTORS},
        SLIDER: slider_calibration,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({motor: asdict(value) for motor, value in combined.items()}, indent=4) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    save_slider_safety_metadata(path, slider_calibration)
    return path


def slider_calibration_from_endpoints(
    slider_id: int,
    endpoint_zero: int,
    endpoint_hundred: int,
) -> MotorCalibration:
    """Map the two endpoints to 0 and 100 while preserving the chosen direction."""
    if endpoint_zero == endpoint_hundred:
        raise ValueError(
            f"Both endpoints read {endpoint_zero}. Move the carriage to two different endpoints."
        )
    for label, endpoint in (("0", endpoint_zero), ("100", endpoint_hundred)):
        if not SLIDER_ENCODER_MIN <= endpoint <= SLIDER_ENCODER_MAX:
            raise ValueError(
                f"The slider.pos={label} endpoint read {endpoint}, outside the supported multi-turn "
                f"range {SLIDER_ENCODER_MIN}..{SLIDER_ENCODER_MAX}."
            )
    travel = abs(endpoint_hundred - endpoint_zero)
    if travel < MIN_SLIDER_TRAVEL_TICKS:
        raise ValueError(
            f"The live-unwrapped endpoints are only {travel} ticks apart ({travel / 4096:.2f} turns). "
            f"Expected at least {MIN_SLIDER_TRAVEL_TICKS} ticks "
            f"({MIN_SLIDER_TRAVEL_TICKS / 4096:.2f} turns)."
        )
    return MotorCalibration(
        id=slider_id,
        drive_mode=0 if endpoint_zero < endpoint_hundred else 1,
        homing_offset=0,
        range_min=min(endpoint_zero, endpoint_hundred),
        range_max=max(endpoint_zero, endpoint_hundred),
    )


def rebase_slider_calibration_for_session(
    calibration: MotorCalibration,
    current_raw: int,
    reference_position: float,
) -> MotorCalibration:
    """Anchor the saved slider travel/direction to one known position for this power session.

    The STS3215 retains its single-turn shaft angle but not the rack's multi-turn
    revolution count across power cycles. The saved calibration therefore remains
    authoritative for travel and direction only; one physical reference is needed
    to recover absolute rail position for each powered session.
    """
    if not math.isfinite(reference_position) or not 0.0 <= reference_position <= 100.0:
        raise ValueError(f"Slider session reference must be within 0..100, got {reference_position!r}.")
    if calibration.drive_mode not in (0, 1):
        raise ValueError(f"Unsupported slider drive_mode={calibration.drive_mode!r}; expected 0 or 1.")
    travel = calibration.range_max - calibration.range_min
    if travel < MIN_SLIDER_TRAVEL_TICKS:
        raise ValueError(
            f"Saved slider travel is only {travel} ticks (minimum {MIN_SLIDER_TRAVEL_TICKS}); "
            "recalibrate ID 7."
        )

    direction_to_hundred = -1 if calibration.drive_mode else 1
    fraction = reference_position / 100.0
    raw_at_zero = round(current_raw - direction_to_hundred * fraction * travel)
    raw_at_hundred = raw_at_zero + direction_to_hundred * travel
    range_min = min(raw_at_zero, raw_at_hundred)
    range_max = max(raw_at_zero, raw_at_hundred)
    if range_min < SLIDER_ENCODER_MIN or range_max > SLIDER_ENCODER_MAX:
        raise ValueError(
            f"Referencing slider.pos={reference_position:g} at raw {current_raw} would create session "
            f"range {range_min}..{range_max}, outside the supported signed multi-turn range "
            f"{SLIDER_ENCODER_MIN}..{SLIDER_ENCODER_MAX}. Power-cycle with torque off and reference "
            "the physical endpoint again."
        )
    return MotorCalibration(
        id=calibration.id,
        drive_mode=calibration.drive_mode,
        homing_offset=calibration.homing_offset,
        range_min=range_min,
        range_max=range_max,
    )


def slider_position_from_raw(
    raw_position: int,
    calibration: MotorCalibration,
    *,
    tolerance_ticks: int = 0,
) -> float:
    """Convert raw slider feedback without silently accepting a stale/out-of-range counter."""
    if tolerance_ticks < 0:
        raise ValueError(f"Slider range tolerance must be non-negative, got {tolerance_ticks}.")
    if not (
        calibration.range_min - tolerance_ticks <= raw_position <= calibration.range_max + tolerance_ticks
    ):
        raise RuntimeError(
            f"Slider raw position {raw_position} is outside this session's referenced range "
            f"{calibration.range_min}..{calibration.range_max}."
        )
    bounded_raw = min(calibration.range_max, max(calibration.range_min, raw_position))
    fraction = (bounded_raw - calibration.range_min) / (calibration.range_max - calibration.range_min)
    return 100.0 - fraction * 100.0 if calibration.drive_mode else fraction * 100.0


def prepare_slider_position_mode(bus: FeetechMotorsBus) -> None:
    """Put only ID 7 in extended multi-turn position mode, with torque disabled."""
    bus.disable_torque([SLIDER])
    bus.write("Operating_Mode", SLIDER, OperatingMode.POSITION.value, normalize=False)
    bus.write("Homing_Offset", SLIDER, 0, normalize=False)
    # On the STS3215 these EEPROM fields are *angle-mode selectors*, not signed
    # extended-position rail limits. Feetech requires both to be zero for
    # multi-turn absolute position control. A sign-encoded negative minimum
    # makes one commanded direction unreachable. The measured rail endpoints
    # remain enforced in software before and throughout every move.
    bus.write("Min_Position_Limit", SLIDER, 0, normalize=False)
    bus.write("Max_Position_Limit", SLIDER, 0, normalize=False)
    angle_limits = {
        "Min_Position_Limit": int(bus.read("Min_Position_Limit", SLIDER, normalize=False)),
        "Max_Position_Limit": int(bus.read("Max_Position_Limit", SLIDER, normalize=False)),
    }
    if any(angle_limits.values()):
        details = ", ".join(f"{register}={value}" for register, value in angle_limits.items())
        raise RuntimeError(
            f"ID 7 rejected multi-turn angle-limit setup ({details}); both registers must read 0."
        )
    phase = int(bus.read("Phase", SLIDER, normalize=False))
    if phase != SLIDER_SAFE_PHASE:
        # Bit 4 enables signed multi-turn feedback. With it cleared,
        # Present_Position wraps to 0..4095 while multi-turn Goal_Position does
        # not, making direction ambiguous at every wrap. Motion callers must
        # still preload the current goal before enabling torque.
        bus.write("Phase", SLIDER, SLIDER_SAFE_PHASE, normalize=False)
    # Goal-register writes can re-enable torque on this ID-7 firmware. Speed is
    # configured only together with an intentional target, never during setup.
    bus.disable_torque([SLIDER])


def _read_slider_position(bus: FeetechMotorsBus) -> int:
    """Read the firmware's signed multi-turn position with Phase bit 4 enabled."""
    return int(bus.read("Present_Position", SLIDER, normalize=False))


def capture_live_slider_endpoint(
    bus: FeetechMotorsBus,
    *,
    label: str,
    reference: int | None = None,
) -> int:
    """Display raw and unwrapped ticks continuously until Enter marks an endpoint."""
    if msvcrt is None:  # pragma: no cover - this repository's hardware host is Windows
        raise RuntimeError("Live slider calibration currently requires a Windows console.")
    print(
        f"\nMove the carriage by hand to slider.pos={label}. "
        "Press ENTER to mark it; signed multi-turn feedback is shown live."
    )
    while True:
        unwrapped = _read_slider_position(bus)
        encoder = unwrapped % 4096
        wraps = unwrapped // 4096
        if reference is None:
            relative_text = "waiting for pos=0 mark"
        else:
            delta = unwrapped - reference
            relative_text = f"from pos=0: {delta:+d} ticks ({delta / 4096:+.3f} turns)"
        line = f"encoder={encoder:4d} | unwrapped={unwrapped:+7d} | wraps={wraps:+3d} | {relative_text}"
        print(f"\r{line:<118}", end="", flush=True)

        if msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in {"\r", "\n"}:
                print()
                return unwrapped
            if key in {"\x1b", "\x03"}:
                print()
                raise KeyboardInterrupt("Slider calibration cancelled.")
            if key in {"\x00", "\xe0"} and msvcrt.kbhit():
                msvcrt.getwch()
        time.sleep(0.05)


def capture_manual_slider_endpoints(bus: FeetechMotorsBus) -> tuple[int, int]:
    """Capture two physical endpoints from signed multi-turn feedback."""
    print(
        "Live fields: encoder is the 0..4095 shaft phase; unwrapped is the servo's signed multi-turn value."
    )
    endpoint_zero = capture_live_slider_endpoint(bus, label="0")
    endpoint_hundred = capture_live_slider_endpoint(
        bus,
        label="100",
        reference=endpoint_zero,
    )
    return endpoint_zero, endpoint_hundred


def calibrate_slider_motor_only(port: str, *, slider_id: int = 7) -> MotorCalibration:
    """Capture manual endpoints using a bus object that contains only motor ID 7.

    Torque is never enabled and Goal_Position is never written. The user, not
    the servo, decides both physical endpoints.
    """
    validate_slider_id(slider_id)
    bus = FeetechMotorsBus(
        port=port,
        motors={SLIDER: Motor(slider_id, "sts3215", MotorNormMode.RANGE_0_100)},
    )
    try:
        bus.connect()
        prepare_slider_position_mode(bus)
        print(f"Motor ID {slider_id} torque is OFF. No movement command will be sent.")
        endpoint_zero, endpoint_hundred = capture_manual_slider_endpoints(bus)
        print(
            f"Captured slider endpoints: pos=0 raw {endpoint_zero}; pos=100 raw {endpoint_hundred}; "
            f"travel {abs(endpoint_hundred - endpoint_zero)} ticks."
        )
        calibration = slider_calibration_from_endpoints(slider_id, endpoint_zero, endpoint_hundred)
        return calibration
    finally:
        if bus.is_connected:
            with suppress(Exception):
                bus.disconnect(disable_torque=True)


class SO101SliderFollower(SOFollower):
    """The stock SO-101 follower plus one multi-turn position-mode slider servo."""

    config_class = SO101SliderFollowerConfig
    name = "so101_slider_follower"

    def __init__(self, config: SO101SliderFollowerConfig):
        validate_slider_id(config.slider_id)
        super().__init__(config)
        self.config = config

        if not self.calibration and config.import_arm_calibration:
            arm_path = stock_arm_calibration_path(self.id)
            if arm_path.is_file():
                self.calibration = load_arm_calibration(arm_path)
                logger.info("Imported existing SO-101 arm calibration from %s", arm_path)

        arm_norm = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100
        self.bus = FeetechMotorsBus(
            port=config.port,
            motors={
                "shoulder_pan": Motor(1, "sts3215", arm_norm),
                "shoulder_lift": Motor(2, "sts3215", arm_norm),
                "elbow_flex": Motor(3, "sts3215", arm_norm),
                "wrist_flex": Motor(4, "sts3215", arm_norm),
                "wrist_roll": Motor(5, "sts3215", arm_norm),
                "gripper": Motor(6, "sts3215", MotorNormMode.RANGE_0_100),
                SLIDER: Motor(config.slider_id, "sts3215", MotorNormMode.RANGE_0_100),
            },
            # Keep the persisted calibration immutable. ID 7 receives an
            # in-memory, power-session-specific range after manual reference.
            calibration=dict(self.calibration),
        )
        self.slider_session_calibration: MotorCalibration | None = None
        self.last_slider_raw_position: int | None = None

    @property
    def is_calibrated(self) -> bool:
        """Treat the complete saved file as authoritative and never prompt for IDs 1..6."""
        if set(self.calibration) != set(ALL_MOTORS):
            return False
        if not slider_safety_metadata_matches(self.calibration_fpath, self.calibration[SLIDER]):
            return False
        expected_ids = {motor: index for index, motor in enumerate(ARM_MOTORS, start=1)}
        expected_ids[SLIDER] = self.config.slider_id
        return all(
            self.calibration[motor].id == expected_ids[motor]
            and self.calibration[motor].range_min < self.calibration[motor].range_max
            and (
                motor != SLIDER
                or self.calibration[motor].range_max - self.calibration[motor].range_min
                >= MIN_SLIDER_TRAVEL_TICKS
            )
            for motor in ALL_MOTORS
        )

    def calibrate(self) -> None:
        if self.is_calibrated:
            return
        if not all(motor in self.calibration for motor in ARM_MOTORS):
            raise RuntimeError(
                "The original six-axis SO-101 calibration is missing. Calibrate the stock arm separately; "
                "the slider-only procedure never changes motor IDs 1..6."
            )
        prepare_slider_position_mode(self.bus)
        arm_calibration = {motor: self.calibration[motor] for motor in ARM_MOTORS}
        slider_calibration = self._capture_connected_slider_endpoints()
        self.calibration = {**arm_calibration, SLIDER: slider_calibration}
        self.bus.calibration = dict(self.calibration)
        self._save_calibration()
        save_slider_safety_metadata(self.calibration_fpath, slider_calibration)
        print(f"Seven-axis calibration saved to {self.calibration_fpath}")

    def _capture_connected_slider_endpoints(self) -> MotorCalibration:
        print("Slider torque is OFF. No movement command will be sent.")
        endpoint_zero, endpoint_hundred = capture_manual_slider_endpoints(self.bus)
        calibration = slider_calibration_from_endpoints(
            self.config.slider_id,
            endpoint_zero,
            endpoint_hundred,
        )
        return calibration

    def reference_slider_session(self, reference_position: float) -> dict[str, float | int]:
        """Reference ID 7 to a known physical position without changing saved calibration."""
        if not self.bus.is_connected:
            raise RuntimeError("Cannot reference the slider before connecting the motor bus.")
        if SLIDER not in self.calibration:
            raise RuntimeError("Saved slider calibration is missing.")

        # All joints remain passive while the operator establishes the rail
        # reference. A raw current-position goal is safe even when the saved
        # normalized range belongs to a previous power session.
        self.bus.sync_write("Torque_Enable", 0, normalize=False, num_retry=5)
        initial_raw = int(self.bus.read("Present_Position", SLIDER, normalize=False, num_retry=3))
        try:
            self.bus.write(
                "Goal_Position",
                SLIDER,
                initial_raw,
                normalize=False,
                num_retry=3,
            )
            time.sleep(0.05)
            goal_raw = int(self.bus.read("Goal_Position", SLIDER, normalize=False, num_retry=3))
            settled_raw = int(self.bus.read("Present_Position", SLIDER, normalize=False, num_retry=3))
            if abs(goal_raw - initial_raw) > 1:
                raise RuntimeError(
                    f"Slider raw session goal did not latch: feedback {initial_raw}, goal {goal_raw}."
                )
            if abs(settled_raw - initial_raw) > SLIDER_RAW_LATCH_TOLERANCE_TICKS:
                raise RuntimeError(
                    f"Slider moved {settled_raw - initial_raw:+d} raw ticks while establishing its "
                    "session reference."
                )
        finally:
            self.bus.sync_write("Torque_Enable", 0, normalize=False, num_retry=5)

        session_calibration = rebase_slider_calibration_for_session(
            self.calibration[SLIDER],
            settled_raw,
            reference_position,
        )
        self.bus.calibration[SLIDER] = session_calibration
        self.slider_session_calibration = session_calibration
        self.last_slider_raw_position = settled_raw
        normalized_position = slider_position_from_raw(settled_raw, session_calibration)
        return {
            "reference_position": float(reference_position),
            "raw_position": settled_raw,
            "normalized_position": normalized_position,
            "session_range_min": session_calibration.range_min,
            "session_range_max": session_calibration.range_max,
            "travel_ticks": session_calibration.range_max - session_calibration.range_min,
            "drive_mode": session_calibration.drive_mode,
        }

    def validate_slider_session_position(self) -> dict[str, float | int]:
        """Fail closed if raw ID-7 feedback leaves the referenced physical rail."""
        if self.slider_session_calibration is None:
            raise RuntimeError(
                "Slider has no power-session reference. With torque off, place it at the requested "
                "physical endpoint and reference it before reading or commanding normalized positions."
            )
        raw_position = int(self.bus.read("Present_Position", SLIDER, normalize=False, num_retry=3))
        try:
            normalized_position = slider_position_from_raw(
                raw_position,
                self.slider_session_calibration,
                tolerance_ticks=SLIDER_SESSION_RANGE_TOLERANCE_TICKS,
            )
        except RuntimeError:
            with suppress(Exception):
                self.bus.sync_write("Torque_Enable", 0, normalize=False, num_retry=5)
            raise
        self.last_slider_raw_position = raw_position
        return {
            "raw_position": raw_position,
            "normalized_position": normalized_position,
            "session_range_min": self.slider_session_calibration.range_min,
            "session_range_max": self.slider_session_calibration.range_max,
        }

    def configure(self) -> None:
        # This mirrors the working stock SOFollower configuration, but leaves
        # torque OFF at the end. Teleop/rollout explicitly enables it only after
        # a fresh target is available, so ID 7 cannot chase a stale goal during
        # connection.
        self.slider_session_calibration = None
        self.last_slider_raw_position = None
        self.bus.calibration = dict(self.calibration)
        self.bus.sync_write("Torque_Enable", 0, normalize=False, num_retry=5)
        self.bus.configure_motors()
        for motor in ARM_MOTORS:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
            self.bus.write("P_Coefficient", motor, 16)
            self.bus.write("I_Coefficient", motor, 0)
            self.bus.write("D_Coefficient", motor, 32)
            if motor == "gripper":
                self.bus.write("Max_Torque_Limit", motor, 500)
                self.bus.write("Protection_Current", motor, 250)
                self.bus.write("Overload_Torque", motor, 25)
        # configure_motors() clears the STS3215 multi-turn phase bit, so apply
        # the full extended-position setup after configuring the six arm motors.
        prepare_slider_position_mode(self.bus)
        slider_raw_position = int(self.bus.read("Present_Position", SLIDER, normalize=False, num_retry=3))
        try:
            # Goal_Position and Goal_Velocity writes can auto-enable ID 7 on this
            # firmware. Raw feedback is mandatory here: the normalized range may
            # belong to a previous power session and can clamp to a false 0/100.
            self.bus.write(
                "Goal_Position",
                SLIDER,
                slider_raw_position,
                normalize=False,
                num_retry=3,
            )
            self.bus.write("Goal_Velocity", SLIDER, 2000, normalize=False)
            time.sleep(0.05)
            settled_raw_position = int(
                self.bus.read("Present_Position", SLIDER, normalize=False, num_retry=3)
            )
            if abs(settled_raw_position - slider_raw_position) > SLIDER_RAW_LATCH_TOLERANCE_TICKS:
                raise RuntimeError(
                    f"Slider moved {settled_raw_position - slider_raw_position:+d} raw ticks while "
                    "latching its fresh startup position."
                )
        finally:
            # Do not let an alarm status from another servo prevent ID 7 from
            # receiving torque-off or prevent the rest of the chain from stopping.
            self.bus.sync_write("Torque_Enable", 0, normalize=False, num_retry=5)


def setup_slider_motor_id(
    port: str,
    *,
    slider_id: int = 7,
    initial_id: int | None = None,
    initial_baudrate: int | None = None,
) -> None:
    """Assign ID 7 while only the new slider motor is attached."""
    validate_slider_id(slider_id)
    bus = FeetechMotorsBus(
        port=port,
        motors={SLIDER: Motor(slider_id, "sts3215", MotorNormMode.RANGE_0_100)},
    )
    try:
        bus.setup_motor(SLIDER, initial_baudrate=initial_baudrate, initial_id=initial_id)
    finally:
        if bus.is_connected:
            bus.disconnect(disable_torque=True)
