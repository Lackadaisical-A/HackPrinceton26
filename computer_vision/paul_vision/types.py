"""Plain data types passed between pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Normalized [x1, y1, x2, y2] in 0..1, in the oriented ("gripper view") frame.
BBox = tuple[float, float, float, float]

STATUS_OK = "ok"
STATUS_NO_TARGET = "no_target"
STATUS_LOW_CONFIDENCE = "low_confidence"
STATUS_DEGRADED = "degraded"
STATUSES = (STATUS_OK, STATUS_NO_TARGET, STATUS_LOW_CONFIDENCE, STATUS_DEGRADED)


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR, HxWx3 uint8, already oriented
    t: float           # capture time, Unix seconds
    seq: int           # sequence number from the source, starting at 1


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: BBox
    source: str = "yolo"


@dataclass(frozen=True)
class Track:
    """A detection seen in the current frame, with an ID that is stable across frames."""
    track_id: int
    label: str
    confidence: float
    bbox: BBox
    source: str = "yolo"


@dataclass(frozen=True)
class Guidance:
    dx: float            # -1..1, positive = target is right of centre
    dy: float            # -1..1, positive = target is below centre
    distance_hint: str   # far | near | in_reach
    aligned: bool
