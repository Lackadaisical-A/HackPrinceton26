"""Spoken summaries from templates: rate-limited, de-duplicated, and only once the wording is stable. Pure logic."""
from __future__ import annotations

from collections.abc import Sequence

from .config import SummaryConfig
from .guidance import schmitt_level
from .phase import (EVENT_DROPPED, EVENT_GRIP, EVENT_RELEASED, EVENT_SENSOR_LOST, PHASE_APPROACH, PHASE_CARRY,
                    PHASE_GRASP, PHASE_PLACE, PHASE_RELEASE, PHASE_SEARCH)
from .types import STATUS_OK, Guidance

DISTANCE_PHRASES = {"far": "ahead", "near": "near", "in_reach": "in reach"}
TEXT_NO_TARGET = "No target."
TEXT_TARGET_LOST = "Target lost."
TEXT_DEGRADED = "Vision feed lost."  # spoken by the transport watchdog, not by Summarizer
TEXT_FIND_DROP = "Looking for the drop station."  # carry with no drop station in view

# One-off events (phase.EVENTS): spoken at once, past the settle time and the rate limit, and never more sure
# than the pads are. A drop is "may have": the pads only know that contact ended away from the drop station.
EVENT_TEXT = {
    EVENT_GRIP: "Grip established.",
    EVENT_RELEASED: "Released.",
    EVENT_DROPPED: "Grip lost. Object may have dropped.",
    EVENT_SENSOR_LOST: "Grip sensor lost.",
}

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
    Events (grip, release, drop, sensor loss) are the exception: they are spoken on the frame they happen. So are
    notices, the label manager's answers ("Learning my flask.", "My flask is ready."), after any events.

    What is wanted depends on the phase (None = search/approach, as in milestone 1):
        search, approach   the target sentence, else "Target lost." / "No target."; nothing over the blind spot
        grasp, release     the target sentence, else nothing (the operator is busy at the gripper)
        carry              the drop station sentence, else "Looking for the drop station."
        place              the drop station sentence, else nothing
    """

    def __init__(self, cfg: SummaryConfig):
        self._cfg = cfg
        self._had_target = False
        self.reset()

    def reset(self) -> None:
        """Forget what was said, so the current state is announced again (used after a stall)."""
        self.forget_target()
        self._spoken: str | None = None
        self._spoken_t: float | None = None

    def forget_target(self) -> None:
        """The target domain changed (objects <-> drop station): the old target is not "lost", it is out of scope.
        What was said and when is kept, so the rate limit still holds."""
        self._had_target = False
        self._horizontal: _Axis | None = None
        self._vertical: _Axis | None = None
        self._wanted: str | None = None
        self._wanted_since = 0.0

    def update(self, status: str, label: str | None, guidance: Guidance | None, t: float,
               target_changed: bool = False, *, phase: str | None = None, events: Sequence[str] = (),
               blind: bool = False, notices: Sequence[str] = ()) -> str | None:
        """Returns the utterance if one is due on this frame, else None. `notices` are spoken now, once each."""
        has_target = status == STATUS_OK and label is not None and guidance is not None
        if has_target:
            if target_changed:
                self._horizontal = self._vertical = None
            self._horizontal = self._axis(guidance.dx, self._horizontal)
            self._vertical = self._axis(guidance.dy, self._vertical)
            direction = direction_phrase(guidance.aligned, self._horizontal, self._vertical)
            text: str | None = f"{label.capitalize()} {DISTANCE_PHRASES[guidance.distance_hint]}, {direction}."
            self._had_target = True
        else:
            self._horizontal = self._vertical = None
            text = self._text_without_target(phase, blind)

        if text != self._wanted:
            self._wanted, self._wanted_since = text, t

        spoken_events = [EVENT_TEXT[e] for e in events if e in EVENT_TEXT]
        if spoken_events or notices:
            utterance = " ".join([*spoken_events, *notices])
            if spoken_events:
                self._spoken = utterance
            # A notice is not the scene's description, so that is still due afterwards (past the rate limit).
            self._spoken_t = t
            return utterance

        if text is None or text == self._spoken or t - self._wanted_since < self._cfg.settle_s:
            return None
        if self._spoken_t is not None and t - self._spoken_t < self._cfg.min_interval_s:
            return None
        self._spoken, self._spoken_t = text, t
        return text

    def _text_without_target(self, phase: str | None, blind: bool) -> str | None:
        if phase in (None, PHASE_SEARCH, PHASE_APPROACH):
            if blind:
                return None  # the gripper is closing over the target: "Target lost." would be wrong and loud
            return TEXT_TARGET_LOST if self._had_target else TEXT_NO_TARGET
        if phase == PHASE_CARRY:
            return TEXT_FIND_DROP
        if phase in (PHASE_GRASP, PHASE_PLACE, PHASE_RELEASE):
            return None
        raise ValueError(f"unknown phase {phase!r}")

    def _axis(self, value: float, prev: _Axis | None) -> _Axis:
        sign = 1 if value >= 0 else -1
        prev_level = prev[0] if prev is not None and prev[1] == sign else None
        bounds = (self._cfg.center_band, self._cfg.slight_band)
        return schmitt_level(abs(value), bounds, prev_level, self._cfg.band_hysteresis), sign
