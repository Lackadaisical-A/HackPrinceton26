"""The `vision` message: building it, the wire encoding, and validation. Pure logic.

Wire format: ONE ZMQ frame, `<topic> <compact utf-8 JSON>`, e.g. b'vision {"t":...}'.
A single frame (not multipart) lets a subscriber set zmq.CONFLATE to keep only the newest message.
"""
from __future__ import annotations

import json
import math

from .guidance import DISTANCE_HINTS
from .phase import ALERTS, PHASES
from .types import STATUS_OK, STATUSES, BBox, Guidance, Track

TOPIC_VISION = "vision"
TOPIC_COMMAND = "command"  # consumed, not published: see commands.py
TOPIC_PRESSURE = "pressure"  # consumed; published by pressure_bridge.py or mock_pressure.py
TOPIC_ROBOT = "robot"  # consumed (gripper_load.py); published by the arm controller, leslider_control/telemetry.py
PRESSURE_PADS = 4  # left tip, left base, right tip, right base (the web console's order)

_BBOX_DECIMALS = 4


def _wire_span(lo: float, hi: float) -> tuple[float, float]:
    """One axis of a box, rounded for the wire and never empty.

    Rounding to 4 decimals can collapse a sliver (x1 = 0.12341, x2 = 0.12344 both become 0.1234), and consumers
    rely on x1 < x2. A collapsed or empty span becomes one step wide, starting at the rounded lower edge, kept
    inside 0..1. Spans that survive rounding are sent exactly as round(v, 4).
    """
    lo, hi = sorted((min(max(lo, 0.0), 1.0), min(max(hi, 0.0), 1.0)))
    a, b = round(lo, _BBOX_DECIMALS), round(hi, _BBOX_DECIMALS)
    if a < b:
        return a, b
    steps = 10 ** _BBOX_DECIMALS
    k = min(round(lo * steps), steps - 1)
    return k / steps, (k + 1) / steps


def wire_bbox(bbox: BBox) -> list[float]:
    """[x1, y1, x2, y2] as published: 4 decimals, inside 0..1, x1 < x2 and y1 < y2."""
    x1, x2 = _wire_span(bbox[0], bbox[2])
    y1, y2 = _wire_span(bbox[1], bbox[3])
    return [x1, y1, x2, y2]


def build_vision_message(*, t: float, frame_id: int, status: str, phase: str, target: Track | None = None,
                         guidance: Guidance | None = None, summary: str | None = None,
                         alert: str | None = None) -> dict:
    """Build the payload. `target` and `guidance` are dropped unless status is `ok`.

    `alert` is null or "dropped" (phase.ALERTS), independent of status: a drop matters whether or not a target
    is in view.
    """
    if status == STATUS_OK and (target is None or guidance is None):
        raise ValueError("status 'ok' needs both a target and guidance")
    if alert is not None and alert not in ALERTS:
        raise ValueError(f"unknown alert {alert!r}; expected one of {ALERTS}")
    ok = status == STATUS_OK
    return {
        "t": round(t, 4),
        "frame_id": frame_id,
        "status": status,
        "phase": phase,
        "target": {
            "track_id": target.track_id,
            "label": target.label,
            "source": target.source,
            "bbox": wire_bbox(target.bbox),
            "confidence": round(target.confidence, 3),
        } if ok else None,
        "guidance": {
            "dx": round(guidance.dx, 3),
            "dy": round(guidance.dy, 3),
            "distance_hint": guidance.distance_hint,
            "aligned": guidance.aligned,
        } if ok else None,
        "summary": summary,
        "alert": alert,
    }


def encode(topic: str, payload: dict) -> bytes:
    return f"{topic} {json.dumps(payload, separators=(',', ':'))}".encode("utf-8")


def decode(data: bytes) -> tuple[str, dict]:
    """Raises ValueError for anything that cannot be read: not UTF-8, not JSON, or JSON nested too deeply."""
    topic, _, body = data.partition(b" ")
    try:
        return topic.decode("utf-8"), json.loads(body)
    except RecursionError:  # json.loads gives up after about a thousand levels of nesting
        raise ValueError("the JSON is nested too deeply to read") from None


def is_number(value) -> bool:
    """A finite int or float, not a bool. False for an int too large for a float, which JSON allows."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_vision(msg: dict) -> None:
    """Raise ValueError if `msg` breaks the vision message contract."""

    def require(ok: bool, problem: str) -> None:
        if not ok:
            raise ValueError(f"invalid vision message: {problem}")

    require(isinstance(msg, dict), "not an object")
    expected = {"t", "frame_id", "status", "phase", "target", "guidance", "summary", "alert"}
    require(set(msg) == expected, f"keys {sorted(msg)} != {sorted(expected)}")
    require(is_number(msg["t"]), "t is not a number")
    require(isinstance(msg["frame_id"], int) and not isinstance(msg["frame_id"], bool), "frame_id is not an int")
    require(msg["status"] in STATUSES, f"unknown status {msg['status']!r}")
    require(msg["phase"] in PHASES, f"unknown phase {msg['phase']!r}")
    require(msg["summary"] is None or (isinstance(msg["summary"], str) and msg["summary"]),
            "summary is neither null nor a non-empty string")
    require(msg["alert"] is None or msg["alert"] in ALERTS, f"unknown alert {msg['alert']!r}")

    target, guidance = msg["target"], msg["guidance"]
    if msg["status"] != STATUS_OK:
        require(target is None and guidance is None, "target/guidance must be null unless status is ok")
        return

    require(isinstance(target, dict) and set(target) == {"track_id", "label", "source", "bbox", "confidence"},
            "target has the wrong fields")
    require(isinstance(target["track_id"], int), "target.track_id is not an int")
    require(isinstance(target["label"], str) and target["label"], "target.label is empty")
    require(isinstance(target["source"], str) and target["source"], "target.source is empty")
    bbox = target["bbox"]
    require(isinstance(bbox, list) and len(bbox) == 4 and all(is_number(v) and 0 <= v <= 1 for v in bbox),
            "target.bbox is not four numbers in 0..1")
    require(bbox[0] < bbox[2] and bbox[1] < bbox[3], "target.bbox is not [x1, y1, x2, y2] with x1 < x2, y1 < y2")
    require(is_number(target["confidence"]) and 0 <= target["confidence"] <= 1, "target.confidence not in 0..1")

    require(isinstance(guidance, dict) and set(guidance) == {"dx", "dy", "distance_hint", "aligned"},
            "guidance has the wrong fields")
    for axis in ("dx", "dy"):
        require(is_number(guidance[axis]) and -1 <= guidance[axis] <= 1, f"guidance.{axis} not in -1..1")
    require(guidance["distance_hint"] in DISTANCE_HINTS, f"unknown distance_hint {guidance['distance_hint']!r}")
    require(isinstance(guidance["aligned"], bool), "guidance.aligned is not a bool")


# --- pressure -------------------------------------------------------------------------------------------------
# pressure {"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}
# `t` is the sample time (Unix seconds, from clock.now() in the publisher). `raw` is what the microcontroller
# read, per pad; `norm` is relative pressure 0..1 per pad after calibration, not a force.


def build_pressure_message(*, t: float, raw, norm) -> dict:
    """Build and validate a pressure payload. Raises ValueError if the result breaks the contract."""

    def compact_raw(v: float):
        # ADC counts arrive as floats from parsing; send them as integers when they are, like the console's example.
        v = float(v)
        return int(v) if v.is_integer() else round(v, 4)

    msg = {
        "t": round(float(t), 4),
        "raw": [compact_raw(v) for v in raw],
        "norm": [round(float(v), 3) for v in norm],
    }
    validate_pressure(msg)
    return msg


def validate_pressure(msg: dict) -> None:
    """Raise ValueError if `msg` breaks the pressure message contract.

    Rejects everything the web console's `validatePressure` rejects (non-object, `t` not a finite number,
    `raw` not exactly four finite numbers, `norm` not exactly four finite numbers in 0..1; booleans are not
    numbers). Stricter in one way, like `validate_vision`: the keys must be exactly t, raw and norm.
    """

    def require(ok: bool, problem: str) -> None:
        if not ok:
            raise ValueError(f"invalid pressure message: {problem}")

    require(isinstance(msg, dict), "not an object")
    expected = {"t", "raw", "norm"}
    require(set(msg) == expected, f"keys {sorted(msg)} != {sorted(expected)}")
    require(is_number(msg["t"]), "t is not a number")
    raw, norm = msg["raw"], msg["norm"]
    require(isinstance(raw, list) and len(raw) == PRESSURE_PADS and all(is_number(v) for v in raw),
            f"raw is not {PRESSURE_PADS} numbers")
    require(isinstance(norm, list) and len(norm) == PRESSURE_PADS and all(is_number(v) and 0 <= v <= 1 for v in norm),
            f"norm is not {PRESSURE_PADS} numbers in 0..1")
