"""Timing: per-stage durations, loop rate and capture-to-publish latency."""
from __future__ import annotations

import logging
import time
from collections import Counter, deque

import numpy as np

log = logging.getLogger("paul_vision.perf")

_HISTORY = 20_000  # samples kept per series for the final table (about ten minutes at 30 FPS)


class Metrics:
    """Collects samples, logs one line per interval and prints a table at the end.

    FPS here is the end-to-end rate of the pipeline (messages published per second), not the camera's.
    """

    def __init__(self, interval_s: float):
        self._interval_s = interval_s
        self._started = self._interval_start = time.perf_counter()
        self._stages: dict[str, deque] = {}
        self._interval_stages: dict[str, list[float]] = {}
        self._latency: deque = deque(maxlen=_HISTORY)
        self._interval_latency: list[float] = []
        self._interval_frames = 0
        self.frames = 0
        self.skipped = 0          # source frames that arrived while the pipeline was busy
        self.statuses: Counter = Counter()
        self.fps = 0.0            # as of the last interval
        self.latency_ms = 0.0     # mean, as of the last interval

    def add(self, stage: str, seconds: float) -> None:
        self._stages.setdefault(stage, deque(maxlen=_HISTORY)).append(seconds)
        self._interval_stages.setdefault(stage, []).append(seconds)

    def frame_done(self, latency_s: float, status: str, skipped: int = 0) -> None:
        self.frames += 1
        self.skipped += skipped
        self.statuses[status] += 1
        self._interval_frames += 1
        self._latency.append(latency_s)
        self._interval_latency.append(latency_s)

    def tick(self) -> None:
        """Log the interval line if one is due."""
        now = time.perf_counter()
        elapsed = now - self._interval_start
        if elapsed < self._interval_s:
            return
        self.fps = self._interval_frames / elapsed
        if self._interval_latency:
            self.latency_ms = 1000 * float(np.mean(self._interval_latency))
            stages = "  ".join(f"{name} {1000 * float(np.mean(v)):.1f}" for name, v in self._interval_stages.items())
            log.info("%.1f FPS | latency %.0f ms (p95 %.0f) | ms per frame: %s", self.fps, self.latency_ms,
                     1000 * float(np.percentile(self._interval_latency, 95)), stages)
        else:
            log.info("0.0 FPS | no frames in the last %.1f s", elapsed)
        self._interval_start = now
        self._interval_frames = 0
        self._interval_latency = []
        self._interval_stages = {}

    def report(self) -> str:
        """A table over the whole run."""
        elapsed = time.perf_counter() - self._started
        lines = [f"{self.frames} frames in {elapsed:.1f} s = {self.frames / elapsed if elapsed else 0:.1f} FPS "
                 f"end to end ({self.skipped} source frames skipped)"]
        if self.frames:
            lines.append("status: " + ", ".join(f"{k} {v}" for k, v in self.statuses.most_common()))
            lines.append(f"{'stage':<26}{'mean ms':>9}{'p50':>8}{'p95':>8}{'max':>8}")
            rows = [(name, np.asarray(v)) for name, v in self._stages.items()]
            rows.append(("capture -> publish", np.asarray(self._latency)))
            for name, values in rows:
                ms = 1000 * values
                lines.append(f"{name:<26}{ms.mean():>9.1f}{np.percentile(ms, 50):>8.1f}"
                             f"{np.percentile(ms, 95):>8.1f}{ms.max():>8.1f}")
        return "\n".join(lines)
