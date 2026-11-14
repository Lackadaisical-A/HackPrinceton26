"""Spoken summaries from templates: rate-limited, de-duplicated, and only once the wording is stable. Pure logic."""
from __future__ import annotations

from .config import SummaryConfig
from .guidance import schmitt_level
from .types import STATUS_OK, Guidance

DISTANCE_PHRASES = {"far": "ahead", "near": "near", "in_reach": "in reach"}
TEXT_NO_TARGET = "No target."
TEXT_TARGET_LOST = "Target lost."
TEXT_DEGRADED = "Vision feed lost."  # spoken by the transport watchdog, not by Summarizer

_Axis = tuple[int, int]  # (level: 0 centred / 1 slight / 2 strong, sign)


def direction_phrase(aligned: bool, horizontal: _Axis, vertical: _Axis) -> str:
    if aligned:
        return "centered"
    parts = []
    if horizontal[0]:
        parts.append((horizontal[0], "right" if horizontal[1] > 0 else "left"))
    if vertical[0]:
        parts.append((vertical[0], "down" if vertical[1] > 0 else "up"))
    if not parts:
        return "nearly centered"
    if len(parts) == 1:
        level, word = parts[0]
        return word if level == 2 else f"slightly {word}"
    (level_a, word_a), (level_b, word_b) = parts
    if level_a == level_b:
        return f"{word_a} and {word_b}" if level_a == 2 else f"slightly {word_a} and {word_b}"
    strong, slight = (word_a, word_b) if level_a == 2 else (word_b, word_a)
    return f"{strong} and slightly {slight}"


class Summarizer:
    """Decides when a new utterance is due.

    An utterance is emitted only when its text differs from the last one spoken, has been the
    wanted text for `settle_s`, and at least `min_interval_s` has passed since the last utterance.
    """

    def __init__(self, cfg: SummaryConfig):
        self._cfg = cfg
        self._had_target = False
        self.reset()

    def reset(self) -> None:
        """Forget what was said, so the current state is announced again (used after a stall)."""
        self._had_target = False
        self._horizontal: _Axis | None = None
        self._vertical: _Axis | None = None
        self._wanted: str | None = None
        self._wanted_since = 0.0
        self._spoken: str | None = None
        self._spoken_t: float | None = None

    def update(self, status: str, label: str | None, guidance: Guidance | None, t: float,
               target_changed: bool = False) -> str | None:
        """Returns the utterance if one is due on this frame, else None."""
        if status == STATUS_OK and label is not None and guidance is not None:
            if target_changed:
                self._horizontal = self._vertical = None
            self._horizontal = self._axis(guidance.dx, self._horizontal)
            self._vertical = self._axis(guidance.dy, self._vertical)
            direction = direction_phrase(guidance.aligned, self._horizontal, self._vertical)
            text = f"{label.capitalize()} {DISTANCE_PHRASES[guidance.distance_hint]}, {direction}."
            self._had_target = True
        else:
            self._horizontal = self._vertical = None
            text = TEXT_TARGET_LOST if self._had_target else TEXT_NO_TARGET

        if text != self._wanted:
            self._wanted, self._wanted_since = text, t
        if text == self._spoken or t - self._wanted_since < self._cfg.settle_s:
            return None
        if self._spoken_t is not None and t - self._spoken_t < self._cfg.min_interval_s:
            return None
        self._spoken, self._spoken_t = text, t
        return text

    def _axis(self, value: float, prev: _Axis | None) -> _Axis:
        sign = 1 if value >= 0 else -1
        prev_level = prev[0] if prev is not None and prev[1] == sign else None
        bounds = (self._cfg.center_band, self._cfg.slight_band)
        return schmitt_level(abs(value), bounds, prev_level, self._cfg.band_hysteresis), sign
