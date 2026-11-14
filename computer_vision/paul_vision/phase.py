"""Task phase reported in every vision message. Pure logic."""
from __future__ import annotations

PHASE_SEARCH = "search"
PHASE_APPROACH = "approach"
PHASES = (PHASE_SEARCH, PHASE_APPROACH, "grasp", "carry", "place", "release")


class PhaseMachine:
    """Milestone 1: `search` until a target is locked, `approach` while one is.

    Milestone 2 adds grasp / carry / place / release, driven by pressure readings, ArUco stations
    and `set_phase` commands. Those transitions belong in this class.
    """

    def __init__(self) -> None:
        self.phase = PHASE_SEARCH

    def update(self, has_target: bool) -> str:
        self.phase = PHASE_APPROACH if has_target else PHASE_SEARCH
        return self.phase
