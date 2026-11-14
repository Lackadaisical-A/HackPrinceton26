"""Box math on normalized [x1, y1, x2, y2] boxes. Pure functions."""
from __future__ import annotations

import math

from .types import BBox


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def center(box: BBox) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def area(box: BBox) -> float:
    """Fraction of the frame the box fills."""
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def iou(a: BBox, b: BBox) -> float:
    inter = area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def center_distance(a: BBox, b: BBox) -> float:
    (ax, ay), (bx, by) = center(a), center(b)
    return math.hypot(ax - bx, ay - by)
