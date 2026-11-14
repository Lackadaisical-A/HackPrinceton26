"""Turn the target box into guidance: smoothed offsets, an `aligned` flag and a distance hint. Pure logic."""
from __future__ import annotations

import math
from collections.abc import Sequence

from .config import DistanceConfig, DistanceThresholds, GuidanceConfig
from .geometry import area, center, clamp
from .types import BBox, Guidance, Track

DISTANCE_HINTS = ("far", "near", "in_reach")


def raw_offset(bbox: BBox) -> tuple[float, float]:
    """Target centre minus image centre, scaled to -1..1. Positive = right / down."""
    cx, cy = center(bbox)
    return clamp((cx - 0.5) * 2.0, -1.0, 1.0), clamp((cy - 0.5) * 2.0, -1.0, 1.0)


def schmitt_level(value: float, bounds: Sequence[float], prev: int | None, hysteresis: float) -> int:
    """Which band `value` is in, given ascending `bounds` (0 = below the first bound).

    With a previous level, the value must clear a bound by the relative `hysteresis` margin before
    the level moves, so a value sitting on a bound does not flicker between two levels.
    """
    if prev is None:
        return sum(value > b for b in bounds)
    level = prev
    while level < len(bounds) and value > bounds[level] * (1.0 + hysteresis):
        level += 1
    while level > 0 and value < bounds[level - 1] * (1.0 - hysteresis):
        level -= 1
    return level


def classify_distance(box_area: float, thresholds: DistanceThresholds, prev: str | None,
                      hysteresis: float) -> str:
    """far / near / in_reach from the fraction of the frame the box fills."""
    prev_level = DISTANCE_HINTS.index(prev) if prev is not None else None
    return DISTANCE_HINTS[schmitt_level(box_area, (thresholds.near, thresholds.in_reach), prev_level, hysteresis)]


class GuidanceFilter:
    """Smooths one target's offsets and box area over time.

    The smoothing is an exponential moving average with a time constant, so it behaves the same at
    any frame rate. Pass reset=True when the target changes, so guidance jumps to the new target
    instead of gliding there from the old one.
    """

    def __init__(self, cfg: GuidanceConfig, distance: DistanceConfig):
        self._cfg = cfg
        self._distance = distance
        self.reset()

    def reset(self) -> None:
        self._state: tuple[float, float, float] | None = None  # smoothed dx, dy, area
        self._t = 0.0
        self._aligned = False
        self._hint: str | None = None

    def update(self, target: Track, t: float, reset: bool = False) -> Guidance:
        if reset:
            self.reset()
        raw = (*raw_offset(target.bbox), area(target.bbox))
        if self._state is None:
            self._state = raw
        else:
            tau = self._cfg.smoothing_tau_s
            alpha = 1.0 - math.exp(-max(0.0, t - self._t) / tau) if tau > 0 else 1.0
            self._state = tuple(s + alpha * (r - s) for s, r in zip(self._state, raw))
        self._t = t
        dx, dy, box_area = self._state

        limit = self._cfg.deadzone_exit if self._aligned else self._cfg.deadzone_enter
        self._aligned = math.hypot(dx, dy) <= limit

        thresholds = self._distance.thresholds.get(target.label, self._distance.thresholds["default"])
        self._hint = classify_distance(box_area, thresholds, self._hint, self._distance.hysteresis)
        return Guidance(dx=dx, dy=dy, distance_hint=self._hint, aligned=self._aligned)
