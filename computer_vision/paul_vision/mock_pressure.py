"""Stand-in for the gripper pressure bridge:  uv run python -m paul_vision.mock_pressure

Binds transport.pressure_sub (like the real bridge) and publishes `pressure` at pressure.mock.rate_hz.
All four pads get the same level, so the grip level is that level under every pressure.combine rule.
A level change ramps over pressure.mock.ramp_s instead of stepping, as a real grip does.

Three ways to drive it:
  (default)             keys in this terminal: space toggles contact (0 <-> pressure.mock.contact_level),
                        0-9 set the level to 0.0-0.9, q or Esc quits. Windows console only (msvcrt); elsewhere
                        it publishes level 0 until stopped.
  --script "0:0,1:0.6,3:0.6,3.5:0"
                        time:level steps, seconds from the start. Each step starts a ramp to its level. Ends
                        when the last ramp has finished, or after --duration.
  --replay FILE         a log written by --log (here or in pressure_bridge), or saved from
                        `subscribe_print --topic pressure --raw`, with its original timing. --loop repeats it.
                        --duration ends it after that many seconds, also in the middle of a pause in the log.

--log FILE writes everything published, in the format --replay reads.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from typing import Callable, Iterable, Sequence

from . import clock
from .config import DEFAULT_CONFIG_PATH, ConfigError, PressureCalibrationConfig, load_config
from .messages import PRESSURE_PADS, TOPIC_PRESSURE, build_pressure_message, decode
from .pressure import PressurePublisher, PressureReading, denormalize

log = logging.getLogger("paul_vision.mock_pressure")

QUIT = object()  # key_target result: stop the mock


# --- levels ------------------------------------------------------------------------------------------------

class Ramp:
    """A level that moves linearly to a new target over `ramp_s`, starting from wherever it is."""

    def __init__(self, ramp_s: float, level: float = 0.0):
        self._ramp_s = ramp_s
        self._from = level
        self._start_t = 0.0
        self.target = level

    def set_target(self, target: float, t: float) -> None:
        self._from = self.value(t)
        self._start_t = t
        self.target = target

    def value(self, t: float) -> float:
        if self._ramp_s <= 0 or t >= self._start_t + self._ramp_s:
            return self.target
        if t <= self._start_t:
            return self._from
        return self._from + (self.target - self._from) * (t - self._start_t) / self._ramp_s


def parse_script(text: str) -> list[tuple[float, float]]:
    """`"0:0, 1:0.6 3:0"` -> [(0.0, 0.0), (1.0, 0.6), (3.0, 0.0)]. Raises ValueError with the bad item."""
    items = [item for item in re.split(r"[,\s]+", text.strip()) if item]
    if not items:
        raise ValueError("the script is empty; expected time:level steps such as 0:0,1:0.6,3:0")
    steps: list[tuple[float, float]] = []
    for item in items:
        t_text, sep, level_text = item.partition(":")
        try:
            if not sep:
                raise ValueError
            t, level = float(t_text), float(level_text)
        except ValueError:
            raise ValueError(f"script step {item!r} is not time:level (for example 1.5:0.6)") from None
        if not (0 <= t < float("inf")):
            raise ValueError(f"script step {item!r}: the time must be 0 or more")
        if not 0 <= level <= 1:
            raise ValueError(f"script step {item!r}: the level must be in 0..1")
        if steps and t <= steps[-1][0]:
            raise ValueError(f"script step {item!r}: times must increase")
        steps.append((t, level))
    return steps


class ScriptLevels:
    """The level at time t (seconds from the start) for a parsed script. Starts at level 0."""

    def __init__(self, steps: Sequence[tuple[float, float]], ramp_s: float):
        self._steps = list(steps)
        self._ramp_s = ramp_s
        self.end_t = self._steps[-1][0] + ramp_s if self._steps else 0.0

    def __call__(self, t: float) -> float:
        ramp = Ramp(self._ramp_s, 0.0)
        for step_t, level in self._steps:
            if step_t > t:
                break
            ramp.set_target(level, step_t)
        return ramp.value(t)


def key_target(key: str, target: float, contact_level: float):
    """The new target level after a key press: a float, QUIT, or None if the key does nothing."""
    if key in ("q", "Q", "\x1b", "\x03"):
        return QUIT
    if key == " ":
        return 0.0 if target > 0 else contact_level
    if len(key) == 1 and key.isdigit():
        return int(key) / 10
    return None


def _console_keys() -> Callable[[], list[str]] | None:
    """A function returning the keys pressed since its last call, or None without a Windows console."""
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        if not sys.stdin.isatty():
            return None
    except (AttributeError, ValueError):
        return None

    def read() -> list[str]:
        keys = []
        while msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):  # arrow and function keys come as two characters; skip both
                msvcrt.getwch()
                continue
            keys.append(key)
        return keys

    return read


def level_message(level: float, t: float, cal: PressureCalibrationConfig) -> dict:
    norm = (min(1.0, max(0.0, level)),) * PRESSURE_PADS
    return build_pressure_message(t=t, raw=[round(v) for v in denormalize(norm, cal)], norm=norm)


def _sleep_until(deadline: float) -> None:
    # time.sleep is high-resolution on Windows since Python 3.11; Event.wait is not.
    delay = deadline - time.perf_counter()
    if delay > 0:
        time.sleep(delay)


def run_levels(publisher: PressurePublisher, level_at: Callable[[float], float | None], rate_hz: float,
               cal: PressureCalibrationConfig, duration: float | None = None,
               stop: Callable[[], bool] = lambda: False) -> int:
    """Publish level_at(seconds from the start) at rate_hz until it returns None, `duration` passes, or stop().

    Paced on absolute deadlines so the rate does not drift; after a hiccup it resynchronizes instead of
    sending a burst. The level is taken at each sample's scheduled time, so a script's curve is the same on
    every run. With `duration`, the last sample sent is the one at exactly `duration`, once: also when
    `duration` is not a whole number of periods, and also when a hiccup made the loop late past it (it is then
    sent at once, alone); nothing is scheduled after it. stop() and a None level end the loop at once, without
    it. Each message is stamped with clock.now() when it is sent. Returns the number sent.
    """
    period = 1.0 / rate_hz
    start = time.perf_counter()
    tick = 0.0  # scheduled time of the next sample, seconds from start
    sent = 0
    while not stop():
        if duration is not None and tick > duration:
            break  # only a negative duration gets here
        level = level_at(tick)
        if level is None:
            break
        publisher.publish(level_message(level, clock.now(), cal))
        sent += 1
        if duration is not None and tick >= duration:
            break  # that was the sample at `duration`
        tick += period
        behind = time.perf_counter() - start
        if tick < behind:
            tick = behind  # fell behind; do not catch up in a burst
        if duration is not None and tick > duration - 1e-6:  # past it, or there but for float rounding
            tick = duration
        _sleep_until(start + tick)
    return sent


# --- replay -----------------------------------------------------------------------------------------------

def read_log(lines: Iterable[str]) -> tuple[list[PressureReading], int]:
    """Readings from log lines (`pressure {json}`), and how many non-empty lines were skipped.

    Lines of other topics, headers and anything invalid are skipped, so `subscribe_print --raw` output works.
    """
    readings, skipped = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith(TOPIC_PRESSURE + " "):
            skipped += 1
            continue
        try:
            readings.append(PressureReading.from_message(decode(line.encode("utf-8"))[1]))
        except ValueError:
            skipped += 1
    return readings, skipped


def retime(readings: Sequence[PressureReading], start_t: float) -> list[tuple[float, PressureReading]]:
    """Each reading with its replay time: the original spacing from `start_t`. Backward steps count as 0."""
    out, t, previous = [], start_t, None
    for reading in readings:
        if previous is not None:
            t += max(0.0, reading.t - previous)
        previous = reading.t
        out.append((t, reading))
    return out


def replay(publisher: PressurePublisher, readings: Sequence[PressureReading], *, loop: bool = False,
           gap_s: float = 0.02, stop: Callable[[], bool] = lambda: False, end_at: float | None = None) -> int:
    """Publish the readings with their original timing, stamped with their replay time. Returns the number sent.

    With `loop`, the log starts again `gap_s` after its last reading. With `end_at` (a time.perf_counter()
    value), no reading due after it is sent, nothing is sent once it has passed, and the replay returns at
    `end_at` instead of sitting out a pause in the log or the gap. Readings that fell due while the process was
    held up are sent at once, in a burst, each stamped with its own replay time: that is deliberate, so the
    log's timing reaches vision unchanged.
    """
    def ended() -> bool:
        return stop() or (end_at is not None and time.perf_counter() > end_at)

    def wait_until(deadline: float) -> bool:
        """Sleep until `deadline`, or only until `end_at` and return False if that comes first."""
        if end_at is not None and deadline > end_at:
            _sleep_until(end_at)
            return False
        _sleep_until(deadline)
        return True

    sent = 0
    while readings and not ended():
        t0_unix, t0_perf = clock.now(), time.perf_counter()
        for replay_t, reading in retime(readings, t0_unix):
            if ended() or not wait_until(t0_perf + (replay_t - t0_unix)):
                return sent
            publisher.publish(build_pressure_message(t=replay_t, raw=reading.raw, norm=reading.norm))
            sent += 1
        if not loop or not wait_until(time.perf_counter() + gap_s):
            break
    return sent


# --- command line -----------------------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.mock_pressure", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--script", help='time:level steps, e.g. "0:0,1:0.6,3:0.6,3.5:0"')
    mode.add_argument("--replay", metavar="FILE", help="replay a pressure log with its original timing")
    parser.add_argument("--loop", action="store_true", help="with --replay: start again at the end")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--rate", type=float, help="publish rate in Hz (default: pressure.mock.rate_hz)")
    parser.add_argument("--endpoint", help="bind here (default: transport.pressure_sub)")
    parser.add_argument("--log", metavar="FILE", help="also write every message published to FILE")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        log.error("configuration: %s", exc)
        return 2
    mock = cfg.pressure.mock
    rate_hz = args.rate if args.rate is not None else mock.rate_hz
    if not rate_hz > 0:
        log.error("--rate must be above 0")
        return 2
    if 1.0 / rate_hz >= cfg.pressure.stale_s:
        log.warning("at %.1f Hz the gap between samples is longer than pressure.stale_s (%.2f s): "
                    "vision will report contact as unknown between them", rate_hz, cfg.pressure.stale_s)
    if args.loop and not args.replay:
        log.error("--loop only applies to --replay")
        return 2

    readings: list[PressureReading] = []
    level_at: Callable[[float], float | None]
    duration = args.duration
    if args.replay:
        try:
            with open(args.replay, encoding="utf-8") as fh:
                readings, skipped = read_log(fh)
        except OSError as exc:
            log.error("cannot read %s: %s", args.replay, exc)
            return 2
        if not readings:
            log.error("%s holds no pressure messages (%d lines skipped)", args.replay, skipped)
            return 2
        span = readings[-1].t - readings[0].t
        log.info("replaying %d readings over %.1f s from %s%s%s", len(readings), span, args.replay,
                 f" ({skipped} other lines skipped)" if skipped else "", ", looping" if args.loop else "")
    elif args.script:
        try:
            script = ScriptLevels(parse_script(args.script), mock.ramp_s)
        except ValueError as exc:
            log.error("--script: %s", exc)
            return 2
        level_at = script
        if duration is None:
            duration = script.end_t
        log.info("script: %s, ramp %.2f s, %.2f s in all", args.script, mock.ramp_s, duration)
    else:
        keys = _console_keys()
        ramp = Ramp(mock.ramp_s, 0.0)
        if keys is None:
            log.warning("no console keyboard here (needs a Windows terminal): publishing level 0 until stopped. "
                        "Use --script or --replay to drive the level.")
            level_at = ramp.value
        else:
            print(f"keys: space toggles contact (0 <-> {mock.contact_level:.2f}), 0-9 set the level 0.0-0.9, "
                  "q or Esc quits", flush=True)

            def level_at(elapsed: float) -> float | None:
                for key in keys():
                    target = key_target(key, ramp.target, mock.contact_level)
                    if target is QUIT:
                        return None
                    if target is not None and target != ramp.target:
                        ramp.set_target(target, elapsed)
                        log.info("level -> %.2f", target)
                return ramp.value(elapsed)

    try:
        publisher = PressurePublisher(args.endpoint or cfg.transport.pressure_sub, log_path=args.log)
    except (RuntimeError, OSError) as exc:
        log.error("%s", exc)
        return 1
    log.info("publishing topic '%s' on %s at %.0f Hz%s", TOPIC_PRESSURE, publisher.endpoint, rate_hz,
             f", logging to {args.log}" if args.log else "")
    start = time.perf_counter()
    end_at = None if duration is None else start + duration
    try:
        if args.replay:
            replay(publisher, readings, loop=args.loop, gap_s=1.0 / rate_hz, end_at=end_at)
        else:
            run_levels(publisher, level_at, rate_hz, cfg.pressure.calibration, duration=duration)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()
    elapsed = time.perf_counter() - start
    print(f"published {publisher.sent} messages in {elapsed:.1f} s ({publisher.sent / max(elapsed, 1e-9):.1f}/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
