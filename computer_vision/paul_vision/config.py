"""Typed configuration, loaded strictly from config.yaml.

The YAML file is the single source of truth: the dataclasses below carry no defaults, so a
missing or misspelled key fails at startup with the key's full path.
"""
from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    default: str | int
    video_end: str


@dataclass(frozen=True)
class CameraConfig:
    wrist_index: int
    backend: str
    width: int
    height: int
    fps: int
    fourcc: str
    rotate_deg: int
    flip_h: bool
    flip_v: bool
    reopen_after_s: float
    first_frame_s: float


@dataclass(frozen=True)
class WeightsConfig:
    torch: str
    openvino: str


@dataclass(frozen=True)
class DetectorConfig:
    backend: str
    device: str | int
    weights: WeightsConfig
    classes: list[str]
    imgsz: list[int]
    conf_min: float
    iou: float
    max_det: int
    agnostic_nms: bool
    models_dir: str


@dataclass(frozen=True)
class LabelsConfig:
    dir: str                        # the label library; relative paths are resolved next to the config file
    max_tags: int
    max_views: int
    max_name_chars: int
    max_prompt_chars: int
    snap_iou: float
    border_margin: float
    min_box_side: float
    worker_timeout_s: float
    worker_threads: int
    load_timeout_s: float
    publish_s: float


@dataclass(frozen=True)
class StationsConfig:
    enabled: bool
    dictionary: str
    pick_id: int
    drop_id: int
    min_side: float
    min_side_ratio: float
    detect_max_px: int
    min_confidence: float
    error_correction_rate: float


def aruco_dictionary_size(name: str) -> int | None:
    """Number of markers in a predefined `DICT_<n>X<n>_<count>` ArUco dictionary, or None for other names.

    Parsed from the name so that loading the config does not need OpenCV.
    """
    parts = name.split("_")
    if len(parts) == 3 and parts[0] == "DICT" and parts[1] in ("4X4", "5X5", "6X6", "7X7") \
            and parts[2] in ("50", "100", "250", "1000"):
        return int(parts[2])
    return None


@dataclass(frozen=True)
class TrackerConfig:
    iou_min: float
    center_dist_max: float
    ttl_s: float
    label_memory_s: float
    label_margin: float


@dataclass(frozen=True)
class SelectionConfig:
    conf_lock: float
    conf_keep: float
    center_weight: float
    coast_s: float
    reacquire_s: float
    reacquire_dist: float
    preferred_label: str | None


@dataclass(frozen=True)
class GuidanceConfig:
    smoothing_tau_s: float
    deadzone_enter: float
    deadzone_exit: float
    aim: list[float]  # [x, y]: where the gripper's grasp point shows in the picture, 0..1; set_aim moves it


@dataclass(frozen=True)
class DistanceThresholds:
    near: float
    in_reach: float


@dataclass(frozen=True)
class DistanceConfig:
    hysteresis: float
    thresholds: dict[str, DistanceThresholds]


@dataclass(frozen=True)
class SummaryConfig:
    min_interval_s: float
    settle_s: float
    center_band: float
    slight_band: float
    band_hysteresis: float


@dataclass(frozen=True)
class PhaseConfig:
    grasp_settle_s: float
    release_hold_s: float
    place_hint: str                 # a distance hint: far | near | in_reach
    place_dwell_s: float
    leave_place_hint: str
    leave_place_dwell_s: float
    alert_hold_s: float
    sensor_loss_hold_s: float
    blind_spot_hint: str
    blind_spot_s: float


@dataclass(frozen=True)
class TransportConfig:
    vision_pub: str
    pressure_sub: str
    command_sub: list[str]          # every `command` sender's endpoint; the last is send_command's default
    robot_sub: str
    send_hwm: int


@dataclass(frozen=True)
class WatchdogConfig:
    stall_timeout_s: float
    heartbeat_s: float


PRESSURE_PADS = 4
PRESSURE_COMBINE_RULES = ("both_fingers", "any_pad", "mean")


@dataclass(frozen=True)
class PressureCalibrationConfig:
    raw_zero: list[float]           # per pad: the raw reading with nothing on it
    raw_full: list[float]           # per pad: the raw reading that counts as norm 1 (may be below raw_zero)


@dataclass(frozen=True)
class PressureMockConfig:
    rate_hz: float
    ramp_s: float
    contact_level: float


@dataclass(frozen=True)
class PressureBridgeConfig:
    baud: int
    read_timeout_s: float
    reopen_s: float
    report_s: float


@dataclass(frozen=True)
class PressureConfig:
    combine: str                    # one of PRESSURE_COMBINE_RULES
    on_threshold: float
    off_threshold: float
    on_hold_s: float
    off_hold_s: float
    stale_s: float
    calibration: PressureCalibrationConfig
    mock: PressureMockConfig
    bridge: PressureBridgeConfig


# The arm controller publishes `robot` at 10 Hz (leslider_control/telemetry.py, RobotTelemetry rate_hz).
ROBOT_TELEMETRY_PERIOD_S = 0.1


@dataclass(frozen=True)
class GripperLoadConfig:
    enabled: bool                   # contact from the gripper servo's load instead of the pads
    sign: int                       # 1 or -1: the load sign that means the jaws press closed
    on_threshold: float
    off_threshold: float
    on_hold_s: float
    off_hold_s: float
    stale_s: float
    empty_pos: float | None         # gripper position with the jaws closed on nothing; None: no check
    empty_margin: float


@dataclass(frozen=True)
class OverlayConfig:
    enabled: bool
    max_width: int


@dataclass(frozen=True)
class StreamConfig:
    enabled: bool
    host: str
    port: int
    max_width: int
    jpeg_quality: int
    max_fps: float


@dataclass(frozen=True)
class VoiceListenConfig:
    culture: str                    # the Windows recognizer, e.g. en-US
    min_confidence: float
    labels_refresh_s: float
    decoy: bool                     # free dictation beside the grammar, never sent
    adaptation: bool                # the recognizer adapting to the speaker
    dictated_names: bool            # tag names by dictation
    decoy_words: list[str]          # sound-alikes of the wake words and labels, in the grammar, never sent


@dataclass(frozen=True)
class VoiceConfig:
    endpoint: str                   # the voice process binds here; must be one of transport.command_sub
    wake_words: list[str]           # [] = every utterance counts
    label_cutoff: float
    label_margin: float
    max_utterance_chars: int        # tag names follow labels.max_name_chars
    deliver_wait_s: float
    listen: VoiceListenConfig


@dataclass(frozen=True)
class LoggingConfig:
    interval_s: float


@dataclass(frozen=True)
class Config:
    source: SourceConfig
    camera: CameraConfig
    detector: DetectorConfig
    labels: LabelsConfig
    stations: StationsConfig
    tracker: TrackerConfig
    selection: SelectionConfig
    guidance: GuidanceConfig
    distance: DistanceConfig
    summary: SummaryConfig
    phase: PhaseConfig
    transport: TransportConfig
    watchdog: WatchdogConfig
    pressure: PressureConfig
    gripper_load: GripperLoadConfig
    overlay: OverlayConfig
    stream: StreamConfig
    voice: VoiceConfig
    logging: LoggingConfig


def _build(tp: Any, value: Any, path: str) -> Any:
    """Convert parsed YAML into the annotated type, rejecting anything that does not fit."""
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
        hints = get_type_hints(tp)
        names = [f.name for f in dataclasses.fields(tp)]
        unknown = sorted(set(value) - set(names))
        missing = sorted(set(names) - set(value))
        if unknown:
            raise ConfigError(f"{path}: unknown key(s) {unknown}")
        if missing:
            raise ConfigError(f"{path}: missing key(s) {missing}")
        return tp(**{n: _build(hints[n], value[n], f"{path}.{n}") for n in names})

    origin = get_origin(tp)
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
        _, value_type = get_args(tp)
        return {str(k): _build(value_type, v, f"{path}.{k}") for k, v in value.items()}
    if origin is list:
        if not isinstance(value, list):
            raise ConfigError(f"{path}: expected a list, got {type(value).__name__}")
        (item_type,) = get_args(tp)
        return [_build(item_type, v, f"{path}[{i}]") for i, v in enumerate(value)]
    if origin in (Union, types.UnionType):
        for option in get_args(tp):
            try:
                return _build(option, value, path)
            except ConfigError:
                continue
        raise ConfigError(f"{path}: {value!r} is not a valid value")

    if tp is type(None):
        if value is None:
            return None
    elif tp is bool:
        if isinstance(value, bool):
            return value
    elif tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif tp is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif tp is str:
        if isinstance(value, str):
            return value
    raise ConfigError(f"{path}: expected {getattr(tp, '__name__', tp)}, got {value!r}")


def validate_pressure_config(p: PressureConfig) -> None:
    """Range checks for the `pressure` section. Separate so that tests and tools can check a section alone."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    require(p.combine in PRESSURE_COMBINE_RULES, f"pressure.combine must be one of {', '.join(PRESSURE_COMBINE_RULES)}")
    require(0 <= p.off_threshold < p.on_threshold <= 1,
            "need 0 <= pressure.off_threshold < pressure.on_threshold <= 1")
    require(p.on_hold_s >= 0 and p.off_hold_s >= 0, "pressure.on_hold_s and pressure.off_hold_s must not be negative")
    require(p.stale_s > 0, "pressure.stale_s must be above 0")
    cal = p.calibration
    require(len(cal.raw_zero) == PRESSURE_PADS and len(cal.raw_full) == PRESSURE_PADS,
            f"pressure.calibration.raw_zero and raw_full need {PRESSURE_PADS} values each (one per pad)")
    require(all(abs(v) < float("inf") for v in (*cal.raw_zero, *cal.raw_full)),  # also False for NaN
            "pressure.calibration values must be finite")
    require(all(z != f for z, f in zip(cal.raw_zero, cal.raw_full)),
            "pressure.calibration: raw_zero and raw_full must differ on every pad")
    require(p.mock.rate_hz > 0, "pressure.mock.rate_hz must be above 0")
    # Slower than this, the detector would call the mock's own feed stale between two samples.
    require(1.0 / p.mock.rate_hz < p.stale_s, "pressure.mock.rate_hz must be above 1 / pressure.stale_s")
    require(p.mock.ramp_s >= 0, "pressure.mock.ramp_s must not be negative")
    require(p.on_threshold <= p.mock.contact_level <= 1,
            "pressure.mock.contact_level must be in [pressure.on_threshold, 1]")
    b = p.bridge
    require(b.baud > 0, "pressure.bridge.baud must be above 0")
    require(b.read_timeout_s > 0 and b.reopen_s > 0 and b.report_s > 0,
            "pressure.bridge.read_timeout_s, reopen_s and report_s must be above 0")


def validate_gripper_load_config(g: GripperLoadConfig) -> None:
    """Range checks for the `gripper_load` section."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    require(g.sign in (1, -1), "gripper_load.sign must be 1 or -1")
    require(0 <= g.off_threshold < g.on_threshold <= 1,
            "need 0 <= gripper_load.off_threshold < gripper_load.on_threshold <= 1")
    require(g.on_hold_s >= 0 and g.off_hold_s >= 0,
            "gripper_load.on_hold_s and gripper_load.off_hold_s must not be negative")
    # At or below the telemetry period, the gap between two ordinary samples would already read as a hole.
    require(g.stale_s > ROBOT_TELEMETRY_PERIOD_S,
            f"gripper_load.stale_s must be above {ROBOT_TELEMETRY_PERIOD_S} s, the arm controller's telemetry period")
    require(g.empty_pos is None or abs(g.empty_pos) < float("inf"), "gripper_load.empty_pos must be finite or null")
    require(0 <= g.empty_margin < float("inf"), "gripper_load.empty_margin must be finite and not negative")


# The distance hints in order of closeness. guidance.DISTANCE_HINTS is the same tuple; it is repeated here because
# guidance imports this module, and a test keeps the two equal.
PHASE_HINT_ORDER = ("far", "near", "in_reach")


def validate_phase_config(p: PhaseConfig) -> None:
    """Range checks for the `phase` section."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    hints = ", ".join(PHASE_HINT_ORDER)
    for key in ("place_hint", "leave_place_hint", "blind_spot_hint"):
        require(getattr(p, key) in PHASE_HINT_ORDER, f"phase.{key} must be one of {hints}")
    # Leaving place must need the station clearly further away than entering it, or place would flicker.
    require(PHASE_HINT_ORDER.index(p.leave_place_hint) < PHASE_HINT_ORDER.index(p.place_hint),
            "phase.leave_place_hint must be further than phase.place_hint (far < near < in_reach)")
    for key in ("grasp_settle_s", "release_hold_s", "place_dwell_s", "leave_place_dwell_s", "sensor_loss_hold_s",
                "blind_spot_s"):
        require(getattr(p, key) >= 0, f"phase.{key} must not be negative")
    require(p.alert_hold_s > 0, "phase.alert_hold_s must be above 0 (the alert must reach at least one message)")


def validate_voice_config(v: VoiceConfig, command_sub: list[str]) -> None:
    """Range checks for the `voice` section. `command_sub` is transport.command_sub: vision connects only there."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    require(v.endpoint in command_sub,
            f"voice.endpoint {v.endpoint} must be one of transport.command_sub ({', '.join(command_sub)}); "
            "vision only connects to those, so it would never hear the voice process")
    require(all(w and w == " ".join(w.lower().split()) for w in v.wake_words),
            "voice.wake_words must be non-empty, lower case, with single spaces")
    require(len(set(v.wake_words)) == len(v.wake_words), "voice.wake_words has duplicates")
    require(0 < v.label_cutoff <= 1, "voice.label_cutoff must be in (0, 1]")
    require(0 <= v.label_margin < 1, "voice.label_margin must be in [0, 1)")
    require(v.max_utterance_chars >= 1, "voice.max_utterance_chars must be at least 1")
    require(v.deliver_wait_s >= 0, "voice.deliver_wait_s must not be negative")
    require(bool(v.listen.culture.strip()), "voice.listen.culture must not be empty")
    require(0 <= v.listen.min_confidence <= 1, "voice.listen.min_confidence must be in [0, 1]")
    require(v.listen.labels_refresh_s > 0, "voice.listen.labels_refresh_s must be above 0")
    decoys = v.listen.decoy_words
    require(all(w and w == " ".join(w.split()) and all(p.isalpha() and p.islower() for p in w.split())
                for w in decoys), "voice.listen.decoy_words must be lower-case words of letters, with single spaces")
    require(len(set(decoys)) == len(decoys), "voice.listen.decoy_words has duplicates")
    require(not set(decoys) & set(v.wake_words), "voice.listen.decoy_words must not hold a wake word")


def validate_labels_config(lb: LabelsConfig) -> None:
    """Range checks for the `labels` section (the operator's tags, M4)."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    require(bool(lb.dir.strip()), "labels.dir must not be empty")
    require(lb.max_tags >= 1, "labels.max_tags must be at least 1")
    require(lb.max_views >= 1, "labels.max_views must be at least 1")
    require(lb.max_name_chars >= 1, "labels.max_name_chars must be at least 1")
    require(lb.max_prompt_chars >= 1, "labels.max_prompt_chars must be at least 1")
    require(0 < lb.snap_iou <= 1, "labels.snap_iou must be in (0, 1]")
    require(0 <= lb.border_margin < 0.25, "labels.border_margin must be in [0, 0.25)")
    require(0 < lb.min_box_side < 1, "labels.min_box_side must be in (0, 1)")
    require(lb.worker_timeout_s > 0, "labels.worker_timeout_s must be above 0")
    require(lb.worker_threads >= 1, "labels.worker_threads must be at least 1")
    require(lb.load_timeout_s > 0, "labels.load_timeout_s must be above 0")
    require(lb.publish_s > 0, "labels.publish_s must be above 0")


def validate(cfg: Config) -> None:
    """Check cross-field constraints that the type system cannot express."""

    def require(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    require(cfg.source.video_end in ("exit", "loop", "hold"), "source.video_end must be exit, loop or hold")
    require(cfg.camera.rotate_deg in (0, 90, 180, 270), "camera.rotate_deg must be 0, 90, 180 or 270")
    require(cfg.camera.backend in ("dshow", "msmf", "any"), "camera.backend must be dshow, msmf or any")
    require(len(cfg.camera.fourcc) in (0, 4), "camera.fourcc must be 4 characters or empty")
    require(cfg.camera.reopen_after_s > 0, "camera.reopen_after_s must be above 0")
    require(cfg.camera.first_frame_s >= cfg.camera.reopen_after_s,
            "camera.first_frame_s must be at least camera.reopen_after_s")
    require(cfg.detector.backend in ("auto", "torch", "openvino"),
            "detector.backend must be auto, torch or openvino")
    require(len(cfg.detector.classes) > 0, "detector.classes must not be empty")
    require(len(set(cfg.detector.classes)) == len(cfg.detector.classes), "detector.classes has duplicates")
    require(len(cfg.detector.imgsz) == 2 and all(v > 0 and v % 32 == 0 for v in cfg.detector.imgsz),
            "detector.imgsz must be [height, width], both positive multiples of 32")
    require(not any("#" in c for c in cfg.detector.classes),
            "detector.classes must not contain '#': it separates a tag's views inside the model (paul_vision.labels)")
    validate_labels_config(cfg.labels)
    stations, size = cfg.stations, aruco_dictionary_size(cfg.stations.dictionary)
    require(size is not None, "stations.dictionary must be DICT_<n>X<n>_<count>, n in 4..7, count 50, 100, 250 "
                              "or 1000 (for example DICT_4X4_50)")
    require(all(0 <= i < size for i in (stations.pick_id, stations.drop_id)),
            f"stations.pick_id and stations.drop_id must be in 0..{size - 1} for {stations.dictionary}")
    require(stations.pick_id != stations.drop_id, "stations.pick_id and stations.drop_id must differ")
    require(0 < stations.min_side <= 0.5, "stations.min_side must be in (0, 0.5]")
    require(0 <= stations.min_side_ratio <= 1, "stations.min_side_ratio must be in [0, 1]")
    require(stations.detect_max_px == 0 or stations.detect_max_px >= 160,
            "stations.detect_max_px must be 0 (full size) or at least 160")
    require(0 <= stations.min_confidence <= 1, "stations.min_confidence must be in [0, 1]")
    require(0 <= stations.error_correction_rate <= 1, "stations.error_correction_rate must be in [0, 1]")
    require(cfg.detector.conf_min <= cfg.selection.conf_keep <= cfg.selection.conf_lock,
            "need detector.conf_min <= selection.conf_keep <= selection.conf_lock")
    require(cfg.guidance.deadzone_enter <= cfg.guidance.deadzone_exit,
            "need guidance.deadzone_enter <= guidance.deadzone_exit")
    require(len(cfg.guidance.aim) == 2 and all(0 <= v <= 1 for v in cfg.guidance.aim),
            "guidance.aim must be [x, y], both in [0, 1]")
    require(0 <= cfg.distance.hysteresis < 1, "distance.hysteresis must be in [0, 1)")
    require("default" in cfg.distance.thresholds, "distance.thresholds needs a `default` entry")
    for label, th in cfg.distance.thresholds.items():
        require(0 < th.near < th.in_reach, f"distance.thresholds.{label}: need 0 < near < in_reach")
        # A box fills at most the whole frame (1.0), and a target already seen must pass in_reach by the
        # hysteresis margin to become in_reach, so at or above 1 it never would.
        require(th.in_reach * (1 + cfg.distance.hysteresis) < 1,
                f"distance.thresholds.{label}: need in_reach * (1 + distance.hysteresis) < 1, "
                f"or a target already seen never becomes in_reach")
    require(cfg.tracker.label_memory_s >= 0 and cfg.tracker.label_margin >= 0,
            "tracker.label_memory_s and tracker.label_margin must not be negative")
    require(0 <= cfg.selection.center_weight <= 1, "selection.center_weight must be in [0, 1]")
    require(cfg.selection.coast_s >= 0 and cfg.selection.reacquire_s >= 0,
            "selection.coast_s and selection.reacquire_s must be 0 or more")
    require(0 < cfg.selection.reacquire_dist <= 1, "selection.reacquire_dist must be in (0, 1]")
    require(0 < cfg.summary.center_band <= cfg.summary.slight_band,
            "need 0 < summary.center_band <= summary.slight_band")
    require(0 <= cfg.summary.band_hysteresis < 1, "summary.band_hysteresis must be in [0, 1)")
    validate_phase_config(cfg.phase)
    commands = cfg.transport.command_sub
    require(len(commands) > 0, "transport.command_sub must list at least one endpoint")
    require(len(set(commands)) == len(commands), "transport.command_sub has duplicates")
    require(cfg.watchdog.stall_timeout_s > 0 and cfg.watchdog.heartbeat_s > 0,
            "watchdog timings must be positive")
    validate_pressure_config(cfg.pressure)
    validate_gripper_load_config(cfg.gripper_load)
    require(bool(cfg.stream.host.strip()), "stream.host must not be empty")
    require(0 <= cfg.stream.port <= 65535, "stream.port must be in 0..65535")
    require(cfg.stream.max_width > 0, "stream.max_width must be positive")
    require(1 <= cfg.stream.jpeg_quality <= 100, "stream.jpeg_quality must be in 1..100")
    require(cfg.stream.max_fps > 0, "stream.max_fps must be positive")
    validate_voice_config(cfg.voice, cfg.transport.command_sub)


def from_dict(data: dict) -> Config:
    cfg = _build(Config, data, "config")
    validate(cfg)
    return cfg


def _yaml_problem(exc: yaml.YAMLError, text: str) -> str:
    """One line for a YAML syntax error in `text`: what the parser saw and where (1-based line and column)."""
    if isinstance(exc, yaml.reader.ReaderError) and isinstance(exc.character, int):  # a character YAML refuses
        at = exc.position
        line, column = text.count("\n", 0, at) + 1, at - text.rfind("\n", 0, at)
        return f"unacceptable character #x{exc.character:04x}: {exc.reason} at line {line}, column {column}"
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None)
    if mark is None or not problem:
        return " ".join(str(exc).split())
    return f"{' '.join(str(problem).split())} at line {mark.line + 1}, column {mark.column + 1}"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """The config at `path`. Raises ConfigError for a file that is not UTF-8 or not valid YAML (the message names the
    file, and for YAML the line and column) and for one that is not a valid config (the message names the key, not
    the file); OSError (FileNotFoundError, PermissionError, ...) when the file cannot be opened."""
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as fh:
        try:
            text = fh.read()
        except UnicodeDecodeError:
            raise ConfigError(f"{path} is not UTF-8 text") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {_yaml_problem(exc, text)}") from None
    cfg = from_dict(data)
    # Paths in the file are relative to the file, so the module runs from any working directory.
    models_dir = (path.parent / cfg.detector.models_dir).resolve()
    labels_dir = (path.parent / cfg.labels.dir).resolve()
    return dataclasses.replace(cfg, detector=dataclasses.replace(cfg.detector, models_dir=str(models_dir)),
                               labels=dataclasses.replace(cfg.labels, dir=str(labels_dir)))
