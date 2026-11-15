"""Choose which tracked object is the target, and hold on to it. Pure logic."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .aruco import SOURCE_ARUCO, STATION_DROP
from .config import SelectionConfig
from .geometry import center, center_distance
from .phase import DOMAIN_OBJECTS, DOMAIN_STATION
from .types import STATUS_LOW_CONFIDENCE, STATUS_NO_TARGET, STATUS_OK, Track

_HALF_DIAGONAL = math.hypot(0.5, 0.5)  # centre-to-corner distance of the normalized frame; the off-aim scale


@dataclass(frozen=True)
class Selection:
    status: str                  # ok | no_target | low_confidence
    target: Track | None = None  # set exactly when status is ok
    fresh: bool = False          # False while coasting on the last seen box
    changed: bool = False        # True on the frame a new target is acquired (not when the same one comes back)


class TargetSelector:
    """Lock the best-scoring object and hold on to that one object.

    Acquiring needs `conf_lock`; holding only needs `conf_keep`. A locked target is never given up for another
    object while it is seen, however well the other scores: only the operator moves the lock (`release()`, which
    select_target and clear_target call), or the loss of the target does. A locked track is also kept when its voted
    label changes, since it is still the same object.

    If the target misses a frame, its last box is held for `coast_s`. After that nothing is published as the target
    for up to `reacquire_s` after the last sighting, while the selector waits for the same object: a confident
    candidate within `reacquire_dist` of where the target was last seen (the tracker gave it a new id, or it was
    hidden for a moment) continues the lock, without `changed`. Any other object is ignored until that time is up;
    then the best-scoring one is locked as a new target.

    The domain (phase.target_domain) says what may be a target at all: in `objects`, anything the object
    detector found (never a station marker), narrowed by the preferred label; in `station`, only the drop
    station's marker. The pick station is never a target. Status counts candidates of the domain only, so a cup
    in view during carry is `no_target`, not `low_confidence`.

    `center_weight` favours objects near the aim point (the gripper's place in the picture, guidance.aim), which is
    the frame centre unless `set_aim` moves it. It only matters when a new target is picked.
    """

    def __init__(self, cfg: SelectionConfig, aim: tuple[float, float] = (0.5, 0.5)):
        self._cfg = cfg
        self._preferred = cfg.preferred_label
        self._aim = (float(aim[0]), float(aim[1]))
        self.domain = DOMAIN_OBJECTS
        self.reset()

    def reset(self) -> None:
        # The locked target as last seen. It stays set while the selector waits for the object to come back, so it
        # is also the memory of where and what the target was.
        self._current: Track | None = None
        self._last_seen = 0.0

    def release(self) -> None:
        """Give up the lock and its memory: the next frame picks the best-scoring candidate afresh."""
        self.reset()

    def set_domain(self, domain: str) -> bool:
        """Switch what may be targeted. A change drops the current target, so it is re-selected in the new domain
        on this very frame and the old one is never published under the new phase. True if it changed."""
        if domain not in (DOMAIN_OBJECTS, DOMAIN_STATION):
            raise ValueError(f"unknown target domain {domain!r}")
        if domain == self.domain:
            return False
        self.domain = domain
        self.reset()
        return True

    def set_preferred_label(self, label: str | None) -> None:
        """Restrict the object target to one label (None = any). Hook for the `select_target` command.

        Stations are not affected: the preference is kept and applies again once objects are targeted."""
        self._preferred = label
        if (self.domain == DOMAIN_OBJECTS and label is not None and self._current is not None
                and self._current.label != label):
            self.reset()

    def set_aim(self, x: float, y: float) -> None:
        """Score candidates by their distance from (x, y). The current target is kept: moving the crosshair is
        not a reason to drop what is being steered to."""
        self._aim = (float(x), float(y))

    def _in_domain(self, tracks: list[Track]) -> list[Track]:
        if self.domain == DOMAIN_STATION:
            return [tr for tr in tracks if tr.source == SOURCE_ARUCO and tr.label == STATION_DROP]
        return [tr for tr in tracks if tr.source != SOURCE_ARUCO]

    def candidates(self, tracks: list[Track]) -> list[Track]:
        """The tracks that may become the target in the current domain."""
        if self.domain == DOMAIN_STATION or self._preferred is None:
            return self._in_domain(tracks)
        return [tr for tr in self._in_domain(tracks) if tr.label == self._preferred]

    def score(self, track: Track) -> float:
        cx, cy = center(track.bbox)
        off_aim = min(1.0, math.hypot(cx - self._aim[0], cy - self._aim[1]) / _HALF_DIAGONAL)
        return track.confidence * (1.0 - self._cfg.center_weight * off_aim)

    def update(self, tracks: list[Track], t: float) -> Selection:
        cfg = self._cfg
        candidates = self.candidates(tracks)
        lockable = [tr for tr in candidates if tr.confidence >= cfg.conf_lock]

        current = self._current
        if current is not None:
            # By id among everything of the domain, not only the preferred label: the label is a vote that can
            # change from one side of the object to another, and the track is still the object that was locked.
            seen = next((tr for tr in self._in_domain(tracks)
                         if tr.track_id == current.track_id and tr.confidence >= cfg.conf_keep), None)
            if seen is not None:
                self._current, self._last_seen = seen, t
                return Selection(STATUS_OK, seen, fresh=True)
            lost_for = t - self._last_seen
            if cfg.coast_s > 0 and lost_for <= cfg.coast_s:
                return Selection(STATUS_OK, current, fresh=False)
            if lost_for <= cfg.reacquire_s:
                near = [tr for tr in lockable if center_distance(tr.bbox, current.bbox) <= cfg.reacquire_dist]
                if near:
                    again = min(near, key=lambda tr: (tr.label != current.label,
                                                      center_distance(tr.bbox, current.bbox)))
                    self._current, self._last_seen = again, t
                    return Selection(STATUS_OK, again, fresh=True)
                # Waiting for the same object: another one in view is not the target, however confident.
                return Selection(STATUS_LOW_CONFIDENCE if candidates else STATUS_NO_TARGET)
            self.reset()

        if lockable:
            return self._lock(max(lockable, key=self.score), t)
        return Selection(STATUS_LOW_CONFIDENCE if candidates else STATUS_NO_TARGET)

    def _lock(self, track: Track, t: float) -> Selection:
        self._current, self._last_seen = track, t
        return Selection(STATUS_OK, track, fresh=True, changed=True)
