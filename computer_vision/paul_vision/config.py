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
    switch_margin: float
    switch_dwell_s: float
    coast_s: float
    preferred_label: str | None


@dataclass(frozen=True)
class GuidanceConfig:
    smoothing_tau_s: float
    deadzone_enter: float
    deadzone_exit: float


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
class TransportConfig:
    vision_pub: str
    pressure_sub: str
    command_sub: str
    robot_sub: str
    send_hwm: int


@dataclass(frozen=True)
class WatchdogConfig:
    stall_timeout_s: float
    heartbeat_s: float


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
class LoggingConfig:
    interval_s: float


@dataclass(frozen=True)
class Config:
    source: SourceConfig
    camera: CameraConfig
    detector: DetectorConfig
    tracker: TrackerConfig
    selection: SelectionConfig
    guidance: GuidanceConfig
    distance: DistanceConfig
    summary: SummaryConfig
    transport: TransportConfig
    watchdog: WatchdogConfig
    overlay: OverlayConfig
    stream: StreamConfig
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
    require(cfg.detector.conf_min <= cfg.selection.conf_keep <= cfg.selection.conf_lock,
            "need detector.conf_min <= selection.conf_keep <= selection.conf_lock")
    require(cfg.guidance.deadzone_enter <= cfg.guidance.deadzone_exit,
            "need guidance.deadzone_enter <= guidance.deadzone_exit")
    require(0 <= cfg.distance.hysteresis < 1, "distance.hysteresis must be in [0, 1)")
    require("default" in cfg.distance.thresholds, "distance.thresholds needs a `default` entry")
    for label, th in cfg.distance.thresholds.items():
        require(0 < th.near < th.in_reach, f"distance.thresholds.{label}: need 0 < near < in_reach")
    require(cfg.tracker.label_memory_s >= 0 and cfg.tracker.label_margin >= 0,
            "tracker.label_memory_s and tracker.label_margin must not be negative")
    require(0 <= cfg.selection.center_weight <= 1, "selection.center_weight must be in [0, 1]")
    require(0 < cfg.summary.center_band <= cfg.summary.slight_band,
            "need 0 < summary.center_band <= summary.slight_band")
    require(0 <= cfg.summary.band_hysteresis < 1, "summary.band_hysteresis must be in [0, 1)")
    require(cfg.watchdog.stall_timeout_s > 0 and cfg.watchdog.heartbeat_s > 0,
            "watchdog timings must be positive")
    require(bool(cfg.stream.host.strip()), "stream.host must not be empty")
    require(0 <= cfg.stream.port <= 65535, "stream.port must be in 0..65535")
    require(cfg.stream.max_width > 0, "stream.max_width must be positive")
    require(1 <= cfg.stream.jpeg_quality <= 100, "stream.jpeg_quality must be in 1..100")
    require(cfg.stream.max_fps > 0, "stream.max_fps must be positive")


def from_dict(data: dict) -> Config:
    cfg = _build(Config, data, "config")
    validate(cfg)
    return cfg


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as fh:
        cfg = from_dict(yaml.safe_load(fh))
    # Paths in the file are relative to the file, so the module runs from any working directory.
    models_dir = (path.parent / cfg.detector.models_dir).resolve()
    return dataclasses.replace(cfg, detector=dataclasses.replace(cfg.detector, models_dir=str(models_dir)))
