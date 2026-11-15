"""Task phase reported in every vision message, and the `dropped` alert. Pure logic: time is passed in.

    search -> approach -> grasp -> carry -> place -> release -> search

The gripper pads (pressure.ContactDetector) decide grasp, carry, release, a failed grasp and a drop. The drop
station's marker decides place. A `set_phase` command forces a phase. Without pad data and without set_phase,
only search and approach occur, named exactly as in milestone 1 (approach while a target is locked).

Each frame takes two steps, because the phase decides what may be targeted and the target decides the rest:
1. update_contact(t, contact): pad evidence and set_phase. Fixes the target domain for this frame.
2. update_vision(t, hint): the distance hint of the target locked in that domain (None if none) names
   search/approach, runs the grasp blind spot and decides carry <-> place. It never changes the domain.

Rules that the transitions keep (each has a test in tests/test_phase.py):
- Unknown contact (state None) never enters grasp, never raises `dropped` and never enters release.
- `dropped` needs contact True seen during the current hold, then False. Stale data in between does not
  excuse the False; stale data alone is never a drop.
- A forced phase sticks: a pad state already present when it was forced does not count as a change. Only a
  state that differs from it (unknown aside) re-arms the pad transitions. If the pads were unknown when it was
  forced, the first state they decide on takes that place.
- A pad feed that dies during a grasp, carry or place that the pads started holds the phase for
  sensor_loss_hold_s, then falls back to search/approach with EVENT_SENSOR_LOST and no alert. If it comes
  back within the hold, nothing happened. If it comes back later, it is a new start: contact True is a new grasp.
  A phase set by command is never dropped for missing pad data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import PhaseConfig
from .guidance import DISTANCE_HINTS

if TYPE_CHECKING:  # pressure imports messages, which imports this module
    from .pressure import Contact

PHASE_SEARCH = "search"
PHASE_APPROACH = "approach"
PHASE_GRASP = "grasp"
PHASE_CARRY = "carry"
PHASE_PLACE = "place"
PHASE_RELEASE = "release"
PHASES = (PHASE_SEARCH, PHASE_APPROACH, PHASE_GRASP, PHASE_CARRY, PHASE_PLACE, PHASE_RELEASE)

# What may be targeted. Objects are the detector's classes; the station is the drop station's marker.
DOMAIN_OBJECTS = "objects"
DOMAIN_STATION = "station"
_STATION_PHASES = (PHASE_CARRY, PHASE_PLACE)

ALERT_DROPPED = "dropped"
ALERTS = (ALERT_DROPPED,)

# One-off events, spoken once by the Summarizer (summary.EVENT_TEXT).
EVENT_GRIP = "grip"                # grasp -> carry: contact held for grasp_settle_s
EVENT_RELEASED = "released"        # place -> release: contact ended at the drop station
EVENT_DROPPED = "dropped"          # carry -> search: contact ended away from the drop station
EVENT_SENSOR_LOST = "sensor_lost"  # pad data missing for sensor_loss_hold_s: back to vision only
EVENTS = (EVENT_GRIP, EVENT_RELEASED, EVENT_DROPPED, EVENT_SENSOR_LOST)

_UNSET = object()          # no forced baseline pending
_FIRST_DECIDED = object()  # forced while the pads were unknown: the first decided state becomes the baseline


def target_domain(phase: str) -> str:
    """What may be targeted in `phase`: the drop station in carry and place, objects otherwise."""
    return DOMAIN_STATION if phase in _STATION_PHASES else DOMAIN_OBJECTS


def _rank(hint: str) -> int:
    return DISTANCE_HINTS.index(hint)


@dataclass(frozen=True)
class PhaseStep:
    """The outcome of one frame."""
    phase: str
    events: tuple[str, ...] = ()
    alert: str | None = None
    blind: bool = False   # approach held over the grasp blind spot: the target is out of sight


class PhaseMachine:
    """search / approach / grasp / carry / place / release, from pad contact, the drop station and set_phase.

    Transitions (at most one pad transition per frame):
        search/approach -> grasp      contact True (a new True: see `forced` below)
        grasp -> carry                contact True held for grasp_settle_s              EVENT_GRIP
        grasp -> search/approach      contact False (a failed grasp: no alert, no event)
        carry -> place                the drop station at place_hint or closer for place_dwell_s
        place -> carry                the drop station at leave_place_hint or further for leave_place_dwell_s,
                                      once it has been seen at place_hint since place began
        place -> release              contact False after True                          EVENT_RELEASED
        carry -> search/approach      contact False after True                          EVENT_DROPPED + alert
        release -> grasp              contact True (gripped again)
        release -> search/approach    after release_hold_s
        grasp/carry/place -> search   no pad data for sensor_loss_hold_s (pad-started phases only) EVENT_SENSOR_LOST
    search and approach are one stage: approach while a target is locked, or over the grasp blind spot.
    """

    def __init__(self, cfg: PhaseConfig):
        self._cfg = cfg
        self._pending: str | None = None
        self.reset()

    def reset(self) -> None:
        """Back to search, forgetting everything (camera switch, new source). A set_phase not applied yet is kept:
        it is the operator's latest word."""
        self.phase = PHASE_SEARCH
        self.forced = False                     # the phase came from set_phase, not from pad evidence
        self._entered_t: float | None = None    # when the current phase began
        self._baseline: object = _UNSET         # pad state at set_phase time; not a change until it changes
        self._held_seen = False                 # contact True seen during the current hold
        self._true_since: float | None = None   # start of the current run of contact True (grasp settle)
        self._unknown_since: float | None = None  # start of the current run without fresh pad data
        self._feed_live = False                 # a fresh pad reading exists (decided or not)
        self._dropped_t: float | None = None    # when the last drop happened (alert hold)
        self._exit_armed = False                # place: the station was seen at place_hint since place began
        self._events: list[str] = []
        self.reset_vision()

    def reset_vision(self) -> None:
        """Forget what was seen, keep the phase. Used when frames stop for a while: a camera hiccup is not a drop."""
        self._dwell_since: float | None = None  # carry <-> place dwell
        self._close_t: float | None = None      # last time the object target was at blind_spot_hint or closer

    def force(self, phase: str) -> None:
        """set_phase: takes effect at the next update_contact, with that frame's pad state as its baseline (or, if
        the pads are unknown on that frame, the first state they decide on afterwards)."""
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}; expected one of {', '.join(PHASES)}")
        self._pending = phase

    def update_contact(self, t: float, contact: Contact | None) -> str:
        """Step 1 of a frame: pad evidence and set_phase. Returns the phase, which fixes the target domain."""
        cfg = self._cfg
        self._events = []
        state = contact.state if contact is not None else None
        self._feed_live = contact is not None and contact.fresh
        if self._feed_live:
            self._unknown_since = None
        elif self._unknown_since is None:
            self._unknown_since = t

        if self._pending is not None:
            self._apply_force(self._pending, t, state)
            self._pending = None
            return self.phase

        # After set_phase, the pad state it was forced in does not count as news; any other known state does. If
        # the pads were unknown then, the first state they decide on is the one already present.
        if self._baseline is _FIRST_DECIDED and state is not None:
            self._baseline = state
        if self._baseline is not _UNSET and state is not None and state != self._baseline:
            self._baseline = _UNSET
        news = self._baseline is _UNSET
        lost = (not self.forced and self._unknown_since is not None
                and t - self._unknown_since >= cfg.sensor_loss_hold_s)

        if self.phase in (PHASE_SEARCH, PHASE_APPROACH):
            if state is True and news:
                self._start_grasp(t)
        elif self.phase == PHASE_GRASP:
            if state is True:
                self._held_seen = True
                if self._true_since is None:
                    self._true_since = t
                if t - self._true_since >= cfg.grasp_settle_s:
                    self._go(PHASE_CARRY, t, EVENT_GRIP)
                    self.forced = False  # the pads have now confirmed the hold themselves
            else:
                self._true_since = None
                if state is False and news:
                    self._object_stage(t)  # failed grasp: the operator sees it, no alert
                elif lost:
                    self._object_stage(t, EVENT_SENSOR_LOST)
        elif self.phase in _STATION_PHASES:
            if state is True:
                self._held_seen = True
            elif state is False and news and self._held_seen:
                if self.phase == PHASE_PLACE:
                    self._go(PHASE_RELEASE, t, EVENT_RELEASED)
                    self.forced = False
                    self._held_seen = False
                else:
                    self._dropped_t = t
                    self._object_stage(t, EVENT_DROPPED)
            elif lost:
                self._object_stage(t, EVENT_SENSOR_LOST)
        elif self.phase == PHASE_RELEASE:
            if state is True and news:
                self._start_grasp(t)  # gripped again before moving away
            elif self._entered_t is not None and t - self._entered_t >= cfg.release_hold_s:
                self._object_stage(t)
        return self.phase

    def update_vision(self, t: float, target_hint: str | None) -> PhaseStep:
        """Step 2 of a frame. `target_hint`: distance_hint of the target locked in this phase's domain, or None."""
        cfg = self._cfg
        blind = False
        if self.phase in (PHASE_SEARCH, PHASE_APPROACH):
            if target_hint is not None:
                self._close_t = t if _rank(target_hint) >= _rank(cfg.blind_spot_hint) else None
                self.phase = PHASE_APPROACH
            elif (self._feed_live and self._close_t is not None and cfg.blind_spot_s > 0
                  and t - self._close_t <= cfg.blind_spot_s):
                # The closing gripper hides a target that was within reach; only the pads can tell what happens
                # next, so the window needs a live pad feed (which also keeps vision-only runs exactly as in M1).
                self.phase = PHASE_APPROACH
                blind = True
            else:
                self._close_t = None
                self.phase = PHASE_SEARCH
        elif self.phase == PHASE_CARRY:
            if target_hint is not None and _rank(target_hint) >= _rank(cfg.place_hint):
                if self._dwell_since is None:
                    self._dwell_since = t
                if t - self._dwell_since >= cfg.place_dwell_s:
                    self._go(PHASE_PLACE, t)
                    self._exit_armed = True
            else:
                self._dwell_since = None
        elif self.phase == PHASE_PLACE:
            if target_hint is not None and _rank(target_hint) >= _rank(cfg.place_hint):
                self._exit_armed = True
            if self._exit_armed and target_hint is not None and _rank(target_hint) <= _rank(cfg.leave_place_hint):
                if self._dwell_since is None:
                    self._dwell_since = t
                if t - self._dwell_since >= cfg.leave_place_dwell_s:
                    self._go(PHASE_CARRY, t)
            else:
                self._dwell_since = None  # sticky: out of sight, or not clearly further away, keeps place
        return PhaseStep(self.phase, tuple(self._events), self.alert(t), blind)

    def step(self, t: float, contact: Contact | None, target_hint: str | None) -> PhaseStep:
        """Both steps with one hint. For tests and callers whose hint does not depend on the domain."""
        self.update_contact(t, contact)
        return self.update_vision(t, target_hint)

    def alert(self, t: float) -> str | None:
        """`dropped` for alert_hold_s after a drop, so that a subscriber keeping only the newest message sees it."""
        if self._dropped_t is not None and 0 <= t - self._dropped_t < self._cfg.alert_hold_s:
            return ALERT_DROPPED
        return None

    # --- helpers ---------------------------------------------------------------------------------------------

    def _go(self, phase: str, t: float, event: str | None = None) -> None:
        if phase != self.phase:
            self._entered_t = t
            self._dwell_since = None
            self._close_t = None
        self.phase = phase
        if event is not None:
            self._events.append(event)

    def _start_grasp(self, t: float) -> None:
        self._go(PHASE_GRASP, t)
        self.forced = False
        self._held_seen = True
        self._true_since = t

    def _object_stage(self, t: float, event: str | None = None) -> None:
        """search/approach; update_vision names it from the lock."""
        self._go(PHASE_SEARCH, t, event)
        self.forced = False
        self._held_seen = False
        self._true_since = None

    def _apply_force(self, phase: str, t: float, state: bool | None) -> None:
        self._go(phase, t)
        self._entered_t = t
        self._dwell_since = None
        # Grasp, carry and place set by hand are kept without pad data; search, approach and release need none.
        self.forced = phase in (PHASE_GRASP, PHASE_CARRY, PHASE_PLACE)
        self._baseline = state if state is not None else _FIRST_DECIDED
        self._held_seen = state is True
        self._true_since = t if state is True else None
        # A place set by hand leaves only once the station has been seen close: it may start far from it.
        self._exit_armed = phase != PHASE_PLACE
