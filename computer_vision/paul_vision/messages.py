"""The `vision` message: building it, the wire encoding, and validation. Pure logic.

Wire format: ONE ZMQ frame, `<topic> <compact utf-8 JSON>`, e.g. b'vision {"t":...}'.
A single frame (not multipart) lets a subscriber set zmq.CONFLATE to keep only the newest message.
"""
from __future__ import annotations

import json
import math

from .guidance import DISTANCE_HINTS
from .phase import PHASES
from .types import STATUS_OK, STATUSES, Guidance, Track

TOPIC_VISION = "vision"
TOPIC_COMMAND = "command"  # consumed, not published: see commands.py


def build_vision_message(*, t: float, frame_id: int, status: str, phase: str, target: Track | None = None,
                         guidance: Guidance | None = None, summary: str | None = None) -> dict:
    """Build the payload. `target` and `guidance` are dropped unless status is `ok`."""
    if status == STATUS_OK and (target is None or guidance is None):
        raise ValueError("status 'ok' needs both a target and guidance")
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
            "bbox": [round(v, 4) for v in target.bbox],
            "confidence": round(target.confidence, 3),
        } if ok else None,
        "guidance": {
            "dx": round(guidance.dx, 3),
            "dy": round(guidance.dy, 3),
            "distance_hint": guidance.distance_hint,
            "aligned": guidance.aligned,
        } if ok else None,
        "summary": summary,
    }


def encode(topic: str, payload: dict) -> bytes:
    return f"{topic} {json.dumps(payload, separators=(',', ':'))}".encode("utf-8")


def decode(data: bytes) -> tuple[str, dict]:
    topic, _, body = data.partition(b" ")
    return topic.decode("utf-8"), json.loads(body)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_vision(msg: dict) -> None:
    """Raise ValueError if `msg` breaks the vision message contract."""

    def require(ok: bool, problem: str) -> None:
        if not ok:
            raise ValueError(f"invalid vision message: {problem}")

    require(isinstance(msg, dict), "not an object")
    expected = {"t", "frame_id", "status", "phase", "target", "guidance", "summary"}
    require(set(msg) == expected, f"keys {sorted(msg)} != {sorted(expected)}")
    require(_is_number(msg["t"]), "t is not a number")
    require(isinstance(msg["frame_id"], int) and not isinstance(msg["frame_id"], bool), "frame_id is not an int")
    require(msg["status"] in STATUSES, f"unknown status {msg['status']!r}")
    require(msg["phase"] in PHASES, f"unknown phase {msg['phase']!r}")
    require(msg["summary"] is None or (isinstance(msg["summary"], str) and msg["summary"]),
            "summary is neither null nor a non-empty string")

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
    require(isinstance(bbox, list) and len(bbox) == 4 and all(_is_number(v) and 0 <= v <= 1 for v in bbox),
            "target.bbox is not four numbers in 0..1")
    require(bbox[0] <= bbox[2] and bbox[1] <= bbox[3], "target.bbox is not [x1, y1, x2, y2]")
    require(_is_number(target["confidence"]) and 0 <= target["confidence"] <= 1, "target.confidence not in 0..1")

    require(isinstance(guidance, dict) and set(guidance) == {"dx", "dy", "distance_hint", "aligned"},
            "guidance has the wrong fields")
    for axis in ("dx", "dy"):
        require(_is_number(guidance[axis]) and -1 <= guidance[axis] <= 1, f"guidance.{axis} not in -1..1")
    require(guidance["distance_hint"] in DISTANCE_HINTS, f"unknown distance_hint {guidance['distance_hint']!r}")
    require(isinstance(guidance["aligned"], bool), "guidance.aligned is not a bool")
