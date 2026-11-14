"""Frame-to-frame identity for detections. Pure logic: time comes in as an argument."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import TrackerConfig
from .geometry import center_distance, iou
from .types import BBox, Detection, Track


@dataclass
class _State:
    track_id: int
    label: str
    source: str
    bbox: BBox
    last_seen: float
    votes: dict[str, float] = field(default_factory=dict)  # label -> confidence added up, fading with time


class IouTracker:
    """Greedy matcher: best overlap first, then nearest centre for boxes that moved too far to overlap.

    Matching ignores the label, because an open-vocabulary detector gives one object different labels
    from different sides: a pill bottle scores "bottle" 0.9 from the side and "cup" 0.5 cap-first. So a
    track's label is decided by vote. Every detection adds its confidence to its label's tally, tallies
    fade with the time constant `label_memory_s`, and the track changes label when another tally is
    `label_margin` ahead of its own. A confident view therefore outlasts a longer doubtful one, while
    a young track corrects a wrong first label within a frame or two.
    """

    def __init__(self, cfg: TrackerConfig):
        self._cfg = cfg
        self._tracks: list[_State] = []
        self._next_id = 1

    def reset(self) -> None:
        """Forget all tracks. IDs keep counting up, so an ID is never reused."""
        self._tracks = []

    def update(self, detections: list[Detection], t: float) -> list[Track]:
        """Match this frame's detections to known tracks. Returns one Track per detection."""
        cfg = self._cfg
        self._tracks = [s for s in self._tracks if t - s.last_seen <= cfg.ttl_s]
        known = list(self._tracks)

        matched: dict[int, _State] = {}  # detection index -> track
        used: set[int] = set()           # indices into `known`

        def assign(pairs: list[tuple[float, int, int]]) -> None:
            for _, ti, di in sorted(pairs):
                if ti not in used and di not in matched:
                    used.add(ti)
                    matched[di] = known[ti]

        def pairs(measure, keep):
            return [
                (cost, ti, di)
                for ti, s in enumerate(known) if ti not in used
                for di, d in enumerate(detections) if di not in matched and d.source == s.source
                for cost in [measure(s.bbox, d.bbox)] if keep(cost)
            ]

        assign(pairs(lambda a, b: -iou(a, b), lambda cost: -cost >= cfg.iou_min))
        assign(pairs(center_distance, lambda cost: cost <= cfg.center_dist_max))

        tracks = []
        for di, det in enumerate(detections):
            state = matched.get(di)
            if state is None:
                state = _State(self._next_id, det.label, det.source, det.bbox, t)
                self._next_id += 1
                self._tracks.append(state)
            self._vote(state, det, t)
            state.bbox, state.last_seen = det.bbox, t
            tracks.append(Track(state.track_id, state.label, det.confidence, det.bbox, det.source))
        return tracks

    def _vote(self, state: _State, det: Detection, t: float) -> None:
        memory = self._cfg.label_memory_s
        fade = math.exp(-(t - state.last_seen) / memory) if memory > 0 else 0.0
        for label in state.votes:
            state.votes[label] *= fade
        state.votes[det.label] = state.votes.get(det.label, 0.0) + det.confidence
        # Only the label that just gained can have overtaken.
        if state.votes[det.label] > state.votes.get(state.label, 0.0) * (1.0 + self._cfg.label_margin):
            state.label = det.label
