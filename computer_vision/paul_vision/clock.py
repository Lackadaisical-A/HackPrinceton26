"""Wall-clock timestamps with sub-millisecond resolution.

Before Python 3.13, time.time() on Windows only ticks every ~15.6 ms, which is too coarse for
stamping 30 FPS frames. Anchor the wall clock once, on a tick edge, and advance it with
perf_counter. Every process that stamps `t` this way agrees to within about a millisecond.
"""
import time


def _anchor() -> tuple[float, float]:
    start = time.time()
    while True:
        wall = time.time()
        if wall != start:  # just crossed a tick edge
            return wall, time.perf_counter()


_WALL0, _PERF0 = _anchor()


def now() -> float:
    """Current time as Unix seconds."""
    return _WALL0 + (time.perf_counter() - _PERF0)
