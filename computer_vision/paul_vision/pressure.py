"""Gripper pressure (milestone M2): the `pressure` topic and the contact decision made from it.

Four thin-film pads sit on the gripper, in the web console's order: left tip, left base, right tip, right base.
A separate process publishes what they read (`pressure_bridge.py` from the microcontroller, `mock_pressure.py`
as a stand-in), binding transport.pressure_sub; vision connects with `PressureReceiver`.

Wire format, one ZMQ frame:  pressure {"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}
`norm` is relative pressure 0..1 per pad after calibration. It is not a force.

`ContactDetector` turns readings into "holding / not holding / unknown". It is pure logic: time comes in as an
argument, so it can be tested and replayed deterministically.

Which clock judges staleness: the reading's own `t` (the sample time the publisher stamped with clock.now()),
compared with the `t` passed to `ContactDetector.update`. Receive time is not used: a reading that sat in a
socket queue while vision stalled is as old as its stamp says, not as new as its arrival. Both clocks are Unix
time from `clock.now()` on the same computer. A stamp ahead of `t` is treated as taken at `t`, so a publisher
whose clock runs ahead can never keep a dead feed looking fresh.

`PressureReceiver.poll` hands over every valid reading that arrived since the last call, and the detector
replays them all on their sample times. So the decision does not depend on how often the main loop gets round
to it: a slow pass delays the decision but cannot lose it, and only a hole in the samples themselves (or a
newest sample older than stale_s) makes the contact unknown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import zmq

from . import clock
from .config import GripperLoadConfig, PressureCalibrationConfig, PressureConfig
from .messages import PRESSURE_PADS, TOPIC_PRESSURE, encode, validate_pressure
from .transport import Subscriber

log = logging.getLogger(__name__)

# How the four pads become one grip level. Explained in config.yaml (pressure.combine).
COMBINE_BOTH_FINGERS = "both_fingers"
COMBINE_ANY_PAD = "any_pad"
COMBINE_MEAN = "mean"
COMBINE_RULES = (COMBINE_BOTH_FINGERS, COMBINE_ANY_PAD, COMBINE_MEAN)


def grip_level(norm: Sequence[float], rule: str) -> float:
    """One grip level 0..1 from the four pads (left tip, left base, right tip, right base).

    both_fingers: min(max(left tip, left base), max(right tip, right base)). A held object presses on both
    jaws, wherever along them it sits; one jaw alone is a bump or a push. any_pad: the largest pad. mean: the
    average of the four.
    """
    if len(norm) != PRESSURE_PADS:
        raise ValueError(f"need {PRESSURE_PADS} pad values, got {len(norm)}")
    if rule == COMBINE_BOTH_FINGERS:
        return min(max(norm[0], norm[1]), max(norm[2], norm[3]))
    if rule == COMBINE_ANY_PAD:
        return max(norm)
    if rule == COMBINE_MEAN:
        return sum(norm) / len(norm)
    raise ValueError(f"unknown pressure combine rule {rule!r}; expected one of {COMBINE_RULES}")


def normalize(raw: Sequence[float], cal: PressureCalibrationConfig) -> tuple[float, ...]:
    """Raw readings to 0..1 per pad: (raw - raw_zero) / (raw_full - raw_zero), clipped.

    raw_full may be below raw_zero (a circuit that reads lower under pressure); the formula covers both.
    """
    if len(raw) != PRESSURE_PADS:
        raise ValueError(f"need {PRESSURE_PADS} raw values, got {len(raw)}")
    return tuple(min(1.0, max(0.0, (r - z) / (f - z))) for r, z, f in zip(raw, cal.raw_zero, cal.raw_full))


def denormalize(norm: Sequence[float], cal: PressureCalibrationConfig) -> tuple[float, ...]:
    """The raw readings that `normalize` maps to `norm`. The mock uses it so its raw values look plausible."""
    return tuple(z + n * (f - z) for n, z, f in zip(norm, cal.raw_zero, cal.raw_full))


@dataclass(frozen=True)
class PressureReading:
    t: float                    # sample time, Unix seconds, stamped by the publisher
    raw: tuple[float, ...]      # per pad, as the microcontroller read it
    norm: tuple[float, ...]     # per pad, 0..1

    @classmethod
    def from_message(cls, msg: dict) -> "PressureReading":
        """Raises ValueError if `msg` breaks the pressure contract."""
        validate_pressure(msg)
        return cls(t=float(msg["t"]), raw=tuple(float(v) for v in msg["raw"]),
                   norm=tuple(float(v) for v in msg["norm"]))


@dataclass(frozen=True)
class Contact:
    """What the gripper pads say, as of one `ContactDetector.update`.

    state: True = holding, False = not holding, None = unknown. Unknown means no fresh reading (none yet, or
        none for longer than pressure.stale_s), or fresh readings that have not yet decided: after unknown,
        the level must stay above on_threshold (or below off_threshold) for the hold time, like any change.
    level: the grip level of the newest fresh reading (pressure.combine), or None when there is none.
    since: when `state` took its current value, on the clock passed to update(). For True or False it is the
        sample time at which the hold time was met; for unknown after stale data, the moment the newest
        reading became stale; at start, the time of the first update().
    """
    state: bool | None
    level: float | None
    since: float

    @property
    def holding(self) -> bool:
        return self.state is True

    @property
    def fresh(self) -> bool:
        """A reading newer than pressure.stale_s exists (even if the state is not decided yet)."""
        return self.level is not None


_EPS_S = 1e-6  # time comparisons: far below the 0.1 ms the wire keeps of `t`, far above float rounding


class ContactDetector:
    """Hysteresis and debounce on the grip level. Pure logic: no I/O, time passed in.

    Contact turns on when the level stays at or above `on_threshold` for `on_hold_s`, and off when it stays at
    or below `off_threshold` for `off_hold_s`. A level between the two thresholds keeps the current state and
    restarts both hold timers. Hold times are measured on sample times, not on update() calls.

    Every reading is taken in turn, however many arrive in one update() call, so the outcome is the same whether
    the samples come one per call or a whole second's worth at once. A reading not newer than the last one taken
    (a repeat, or out of order) is ignored, so the readings taken are always in sample-time order.

    Stale data never reads as holding or not holding: if the newest reading is more than `stale_s` older than
    `t`, the state is None. Two consecutive readings more than `stale_s` apart (the feed was interrupted, or the
    socket queue overflowed and lost the samples in between) also reset to None, whether they arrive in one call
    or in two, because what happened in between is unknown.

    The same logic serves any grip signal. By default a reading is a PressureReading and its level is the pads'
    grip_level (pressure.combine). With `level_of`, a reading is anything with a sample time `t`, and `level_of`
    gives its level 0..1: gripper_load.py passes the gripper servo's load this way, with the `gripper_load`
    section as `cfg` (only the thresholds, hold times and stale_s are read from it).
    """

    def __init__(self, cfg: PressureConfig | GripperLoadConfig, level_of: Callable[[Any], float] | None = None):
        if level_of is None:
            if cfg.combine not in COMBINE_RULES:
                raise ValueError(f"unknown pressure.combine {cfg.combine!r}; expected one of {COMBINE_RULES}")
            level_of = lambda reading: grip_level(reading.norm, cfg.combine)  # noqa: E731
        self._cfg = cfg
        self._level_of = level_of
        self.reset()

    @property
    def stale_s(self) -> float:
        """No reading newer than this means unknown. The main loop polls at least twice per stale_s."""
        return self._cfg.stale_s

    def reset(self) -> None:
        self._state: bool | None = None
        self._since: float | None = None
        self._level: float | None = None
        self._last_t: float | None = None         # sample time of the newest accepted reading
        self._above_since: float | None = None    # first sample of the current run at or above on_threshold
        self._below_since: float | None = None    # first sample of the current run at or below off_threshold

    def update(self, readings: Any | Iterable[Any] | None, t: float) -> Contact:
        """Take the readings that arrived since the last call and return the contact state at time t.

        `readings` is what PressureReceiver.poll() returned: every new reading in arrival order, or None if
        nothing new arrived. A single reading is also accepted. Pass `t` from clock.now() (the same clock
        the publisher stamps with), taken when the readings were polled. Freshness is judged once, on the newest
        reading against `t`.
        """
        if self._since is None:
            self._since = t
        if hasattr(readings, "t"):  # a single reading
            readings = (readings,)
        for reading in readings or ():
            self._accept(reading, t)
        stale_s = self._cfg.stale_s
        if self._last_t is None or t - self._last_t > stale_s + _EPS_S:
            self._go_unknown(self._last_t + stale_s if self._last_t is not None else t)
            return Contact(None, None, self._since)
        return Contact(self._state, self._level, self._since)

    def _accept(self, reading: Any, t: float) -> None:
        cfg = self._cfg
        sample_t = min(reading.t, t)  # a stamp from the future counts as taken now
        if self._last_t is not None and sample_t <= self._last_t:
            return  # already seen, or older than what was seen
        if self._last_t is not None and sample_t - self._last_t > cfg.stale_s + _EPS_S:
            self._go_unknown(self._last_t + cfg.stale_s)  # a hole in the feed: what happened in it is unknown
        self._last_t = sample_t
        level = self._level_of(reading)
        self._level = level
        if level >= cfg.on_threshold:
            self._below_since = None
            if self._above_since is None:
                self._above_since = sample_t
            if self._state is not True and sample_t - self._above_since >= cfg.on_hold_s - _EPS_S:
                self._state, self._since = True, sample_t
        elif level <= cfg.off_threshold:
            self._above_since = None
            if self._below_since is None:
                self._below_since = sample_t
            if self._state is not False and sample_t - self._below_since >= cfg.off_hold_s - _EPS_S:
                self._state, self._since = False, sample_t
        else:  # between the thresholds: keep the state, and neither change has been held
            self._above_since = self._below_since = None

    def _go_unknown(self, at: float) -> None:
        if self._state is not None:
            self._state, self._since = None, at
        self._level = None
        self._above_since = self._below_since = None


class FeedReceiver:
    """Reads one topic's messages without waiting and turns each into a reading for ContactDetector.

    The main loop calls poll() on every pass and hands the result to ContactDetector.update, which needs every
    reading, not only the newest (see the module docstring). The publisher binds and this side connects, in
    either order. `parse` turns a payload into a reading, returns None for a valid message that carries no
    reading, or raises ValueError for an invalid one. Invalid messages are ignored: the first one is logged,
    later ones are counted and summarized at most once every _WARN_EVERY_S, so a broken publisher at 50 Hz
    cannot flood the log. `name` starts the log lines.
    """

    # ZMQ queues about 1000 messages per side (its default high-water mark) and drops new ones beyond that, so
    # after a long stall the queue holds the oldest samples and the hole after them shows as a gap in sample
    # times. One call reads at most this many, a few milliseconds' work; anything left waits for the next call
    # and continues in order.
    _MAX_PER_POLL = 1024
    _WARN_EVERY_S = 5.0

    def __init__(self, endpoint: str, topic: str, parse: Callable[[Any], Any | None], name: str):
        self.endpoint = endpoint
        self.name = name
        self._parse = parse
        try:
            self._sub = Subscriber(endpoint, topic=topic)
        except zmq.ZMQError as exc:
            raise RuntimeError(f"could not connect the {name} subscriber to {endpoint}: {exc}") from exc
        self.received = 0           # valid readings
        self.without_reading = 0    # valid messages that carried no reading
        self.rejected = 0           # invalid messages ignored
        self._warned_at: float | None = None
        self._unreported = 0
        self._last_problem = ""

    def poll(self) -> list[Any] | None:
        """Every valid reading that arrived since the last call, in arrival order, or None if none did.

        Never blocks. Pass the result to ContactDetector.update as it is.
        """
        readings: list[Any] = []
        for _ in range(self._MAX_PER_POLL):
            try:
                item = self._sub.recv(0)
            except ValueError as exc:  # not UTF-8 or not JSON
                self._reject(f"unreadable: {exc}")
                continue
            if item is None:
                break
            try:
                reading = self._parse(item[1])
            except ValueError as exc:
                self._reject(str(exc))
                continue
            if reading is None:
                self.without_reading += 1
                continue
            readings.append(reading)
            self.received += 1
        self._report_rejects()
        return readings or None

    def _reject(self, problem: str) -> None:
        self.rejected += 1
        problem = problem[:160]
        if self._warned_at is None:
            log.warning("%s: ignored a message: %s (further ones are summarized every %.0f s)",
                        self.name, problem, self._WARN_EVERY_S)
            self._warned_at = clock.now()
        else:
            self._unreported += 1
            self._last_problem = problem

    def _report_rejects(self) -> None:
        if not self._unreported or self._warned_at is None:
            return
        now = clock.now()
        if now - self._warned_at >= self._WARN_EVERY_S:
            log.warning("%s: ignored %d more invalid messages in the last %.0f s (latest: %s)",
                        self.name, self._unreported, now - self._warned_at, self._last_problem)
            self._warned_at, self._unreported = now, 0

    def close(self) -> None:
        self._sub.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class PressureReceiver(FeedReceiver):
    """Reads `pressure` messages as PressureReadings. The bridge or the mock binds transport.pressure_sub."""

    kind = "pads"  # for the overlay's grip line

    def __init__(self, endpoint: str):
        super().__init__(endpoint, TOPIC_PRESSURE, PressureReading.from_message, "pressure")


class PressurePublisher:
    """Binds the `pressure` endpoint and sends messages; used by the bridge and the mock, never by vision.

    With `log_path`, every message sent is also written to that file as its wire text, one per line. That is
    the format `mock_pressure.py --replay` reads (and the output of `subscribe_print --topic pressure --raw`).
    """

    def __init__(self, endpoint: str, log_path: str | Path | None = None):
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.set(zmq.LINGER, 0)
        try:
            self._sock.bind(endpoint)
        except zmq.ZMQError as exc:
            self._sock.close(linger=0)
            self._ctx.term()
            raise RuntimeError(f"could not bind the pressure publisher to {endpoint}: {exc}. "
                               "Is another mock or bridge running?") from exc
        self.endpoint = self._sock.last_endpoint.decode("utf-8")  # the real port when bound to port 0
        self.sent = 0
        self._log = None
        if log_path is not None:
            try:
                # Line-buffered, so a log cut short by Ctrl+C or a crash still holds every whole line sent.
                self._log = open(log_path, "w", encoding="utf-8", newline="\n", buffering=1)
            except OSError:
                self.close()
                raise

    def publish(self, msg: dict) -> None:
        """Send one payload (built with messages.build_pressure_message, which validates it)."""
        data = encode(TOPIC_PRESSURE, msg)
        self._sock.send(data)
        if self._log is not None:
            self._log.write(data.decode("utf-8") + "\n")
        self.sent += 1

    def close(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None
        if not self._sock.closed:
            self._sock.close(linger=0)
            self._ctx.term()

    def __enter__(self) -> "PressurePublisher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
