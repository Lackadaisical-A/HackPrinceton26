"""Choose which tracked object is the target, and hold on to it. Pure logic."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .aruco import SOURCE_ARUCO, STATION_DROP
from .config import SelectionConfig
from .geometry import center
from .phase import DOMAIN_OBJECTS, DOMAIN_STATION
from .types import STATUS_LOW_CONFIDENCE, STATUS_NO_TARGET, STATUS_OK, Track

_HALF_DIAGONAL = math.hypot(0.5, 0.5)  # centre-to-corner distance of the normalized frame


@dataclass(frozen=True)
class Selection:
    status: str                  # ok | no_target | low_confidence
    target: Track | None = None  # set exactly when status is ok
    fresh: bool = False          # False while coasting on the last seen box
    changed: bool = False        # True on the frame a target is acquired or switched


class TargetSelector:
    """Lock the best-scoring object and keep it until it is lost or clearly beaten.

    Acquiring needs `conf_lock`; holding only needs `conf_keep`. A rival must outscore the current
    target by `switch_margin` for `switch_dwell_s` before the target switches. If the target misses
    a frame, its last box is held for `coast_s` before it is dropped.

    The domain (phase.target_domain) says what may be a target at all: in `objects`, anything the object
    detector found (never a station marker), narrowed by the preferred label; in `station`, only the drop
    station's marker. The pick station is never a target. Status counts candidates of the domain only, so a cup
    in view during carry is `no_target`, not `low_confidence`.
    """

    def __init__(self, cfg: SelectionConfig):
        self._cfg = cfg
        self._preferred = cfg.preferred_label
        self.domain = DOMAIN_OBJECTS
        self.reset()

    def reset(self) -> None:
        self._current: Track | None = None
        self._last_seen = 0.0
        self._rival_id: int | None = None
        self._rival_since = 0.0

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

    def candidates(self, tracks: list[Track]) -> list[Track]:
        """The tracks that may be the target in the current domain."""
        if self.domain == DOMAIN_STATION:
            return [tr for tr in tracks if tr.source == SOURCE_ARUCO and tr.label == STATION_DROP]
        return [tr for tr in tracks
                if tr.source != SOURCE_ARUCO and (self._preferred is None or tr.label == self._preferred)]

    def score(self, track: Track) -> float:
        cx, cy = center(track.bbox)
        off_center = min(1.0, math.hypot(cx - 0.5, cy - 0.5) / _HALF_DIAGONAL)
        return track.confidence * (1.0 - self._cfg.center_weight * off_center)

    def update(self, tracks: list[Track], t: float) -> Selection:
        cfg = self._cfg
        candidates = self.candidates(tracks)
        lockable = [tr for tr in candidates if tr.confidence >= cfg.conf_lock]

        if self._current is not None:
            seen = next((tr for tr in candidates
                         if tr.track_id == self._current.track_id and tr.confidence >= cfg.conf_keep), None)
            if seen is not None:
                self._current, self._last_seen = seen, t
                rival = max((tr for tr in lockable if tr.track_id != seen.track_id),
                            key=self.score, default=None)
                if rival is not None and self.score(rival) > self.score(seen) * (1.0 + cfg.switch_margin):
                    if rival.track_id != self._rival_id:
                        self._rival_id, self._rival_since = rival.track_id, t
                    if t - self._rival_since >= cfg.switch_dwell_s:
                        return self._lock(rival, t)
                else:
                    self._rival_id = None
                return Selection(STATUS_OK, seen, fresh=True)
            if cfg.coast_s > 0 and t - self._last_seen <= cfg.coast_s:
                return Selection(STATUS_OK, self._current, fresh=False)
            self.reset()

        if lockable:
            return self._lock(max(lockable, key=self.score), t)
        return Selection(STATUS_LOW_CONFIDENCE if candidates else STATUS_NO_TARGET)

    def _lock(self, track: Track, t: float) -> Selection:
        self._current, self._last_seen, self._rival_id = track, t, None
        return Selection(STATUS_OK, track, fresh=True, changed=True)
