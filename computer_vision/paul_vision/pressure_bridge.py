"""Gripper pressure from the microcontroller:  uv run python -m paul_vision.pressure_bridge --port COM5

Reads text lines over USB serial and publishes each as a `pressure` message on transport.pressure_sub.

Expected line format (an assumption until the firmware exists): one line per sample, four raw readings in pad
order (left tip, left base, right tip, right base), separated by commas or by spaces, ending in a newline:
    1840,1322,1795,1290
Anything else is skipped and counted: blank lines, headers such as `lt,lb,rt,rb`, debug text, lines with the
wrong number of fields or with something that is not a finite number. The first line after the port opens is
always skipped, because it may be the tail of a line cut in half. A single trailing separator is accepted.

Raw values become `norm` (0..1 per pad) with pressure.calibration. The message is stamped with clock.now()
when its line is read, because the microcontroller's own clock is not on Unix time.

The port may disappear (cable pulled, board reset) and come back: the bridge retries every
pressure.bridge.reopen_s. Find the port with:  uv run python -m serial.tools.list_ports

Needs pyserial, which is not a dependency of this project yet. Everything except opening the port is plain
Python and tested without it.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import sys
import time
from typing import Callable, Sequence

from . import clock
from .config import DEFAULT_CONFIG_PATH, ConfigError, PressureBridgeConfig, PressureConfig, load_config
from .messages import PRESSURE_PADS, TOPIC_PRESSURE, build_pressure_message
from .pressure import PressurePublisher, normalize

log = logging.getLogger("paul_vision.pressure_bridge")

PYSERIAL_MISSING = ("pressure_bridge needs pyserial, which is not installed. "
                    "Fix: uv add pyserial (in computer_vision/, once the owner has approved the dependency)")

SKIP_BLANK = "blank"
SKIP_FIELDS = "wrong field count"
SKIP_NUMBER = "not a number"
SKIP_TOO_LONG = "too long"
SKIP_FIRST = "first line after opening"


class SkippedLine(ValueError):
    def __init__(self, reason: str, line: str = ""):
        super().__init__(f"{reason}: {line!r}")
        self.reason = reason
        self.line = line


def parse_line(text: str) -> tuple[float, ...]:
    """Four raw readings from one line, or SkippedLine with the reason.

    Fields are separated by commas if the line has any, else by whitespace. Empty fields are never dropped
    (`1,,2,3,4` is wrong, not `1,2,3,4`), because dropping one would shift every later pad onto the wrong
    finger. One trailing separator is allowed, as firmware that prints a comma after every value produces.
    """
    line = text.strip()
    if not line:
        raise SkippedLine(SKIP_BLANK, text)
    if "," in line:
        fields = [f.strip() for f in line.split(",")]
        if len(fields) == PRESSURE_PADS + 1 and fields[-1] == "":
            fields.pop()
    else:
        fields = line.split()
    if len(fields) != PRESSURE_PADS:
        raise SkippedLine(SKIP_FIELDS, text)
    try:
        values = tuple(float(f) for f in fields)
    except ValueError:
        raise SkippedLine(SKIP_NUMBER, text) from None
    if not all(math.isfinite(v) for v in values):
        raise SkippedLine(SKIP_NUMBER, text)
    return values


class LineSplitter:
    """Bytes from the port in, complete lines out. A line longer than `max_len` is discarded (yielded as None)."""

    def __init__(self, max_len: int = 256):
        self._max_len = max_len
        self._buf = bytearray()
        self._discarding = False  # inside an overlong line, until its newline

    def feed(self, data: bytes) -> list[str | None]:
        lines: list[str | None] = []
        self._buf += data
        while True:
            end = self._buf.find(b"\n")
            if end < 0:
                break
            raw = bytes(self._buf[:end])
            del self._buf[:end + 1]
            if self._discarding:
                self._discarding = False
                continue  # the rest of a line already reported as too long
            lines.append(None if len(raw) > self._max_len else raw.decode("utf-8", errors="replace").strip())
        if len(self._buf) > self._max_len:
            if not self._discarding:
                lines.append(None)
            self._discarding = True
            self._buf.clear()
        return lines


class PressureBridge:
    """Turns serial bytes into `pressure` messages. No I/O of its own: `publish` and `now` are passed in."""

    def __init__(self, cfg: PressureConfig, publish: Callable[[dict], None], now: Callable[[], float] = clock.now,
                 echo: Callable[[str], None] | None = None):
        self._cfg = cfg
        self._publish = publish
        self._now = now
        self._echo = echo
        self._splitter = LineSplitter()
        self._skip_first = True
        self.published = 0
        self.skipped: dict[str, int] = {}       # reason -> count, since start
        self._window_published = 0
        self._window_skipped: dict[str, int] = {}
        self._report_t = now()

    def port_opened(self) -> None:
        """Call after every (re)open: forget any half line and skip the first line, which may be cut."""
        self._splitter = LineSplitter()
        self._skip_first = True

    def feed(self, data: bytes) -> int:
        """Process bytes read from the port. Returns how many messages were published."""
        count = 0
        for line in self._splitter.feed(data):
            if line is None:
                self._skip(SKIP_TOO_LONG, "")
                self._skip_first = False  # it was the cut line, if any; the next one is whole
                continue
            if self._echo is not None:
                self._echo(line)
            if self._skip_first:
                self._skip_first = False
                self._skip(SKIP_FIRST, line)
                continue
            try:
                raw = parse_line(line)
            except SkippedLine as exc:
                self._skip(exc.reason, line)
                continue
            self._publish(build_pressure_message(t=self._now(), raw=raw,
                                                 norm=normalize(raw, self._cfg.calibration)))
            count += 1
        self.published += count
        self._window_published += count
        return count

    def _skip(self, reason: str, line: str) -> None:
        first = reason not in self.skipped
        self.skipped[reason] = self.skipped.get(reason, 0) + 1
        self._window_skipped[reason] = self._window_skipped.get(reason, 0) + 1
        if reason == SKIP_FIRST:
            log.debug("skipped the first line after opening: %r", line[:80])
        elif first:  # later ones only appear in the periodic report
            log.info("skipped a line (%s): %r", reason, line[:80])

    def report_if_due(self) -> None:
        """Log one status line every pressure.bridge.report_s."""
        now = self._now()
        window = now - self._report_t
        if window < self._cfg.bridge.report_s:
            return
        skipped = ", ".join(f"{reason} {n}" for reason, n in sorted(self._window_skipped.items()))
        if self._window_published:
            log.info("published %d samples in %.0f s (%.1f/s)%s", self._window_published, window,
                     self._window_published / window, f"; skipped: {skipped}" if skipped else "")
        else:
            log.warning("no samples in the last %.0f s%s", window,
                        f" (lines skipped: {skipped})" if skipped else " (nothing received)")
        self._report_t = now
        self._window_published = 0
        self._window_skipped = {}


def run_bridge(bridge: PressureBridge, open_port: Callable[[], object], cfg: PressureBridgeConfig, name: str,
               port_errors: tuple[type[BaseException], ...] = (OSError,),
               stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep) -> None:
    """Read the port and feed the bridge until stop(). Opens the port, and reopens it whenever it is lost.

    `open_port()` returns an object with `read(n) -> bytes`, `in_waiting` and `close()` (a pyserial Serial).
    """
    missing_reported = False
    while not stop():
        try:
            port = open_port()
        except port_errors as exc:
            if not missing_reported:
                log.warning("cannot open %s (%s); trying again every %.1f s", name, exc, cfg.reopen_s)
                missing_reported = True
            bridge.report_if_due()
            sleep(cfg.reopen_s)
            continue
        log.info("%s open at %d baud", name, cfg.baud)
        missing_reported = False
        bridge.port_opened()
        try:
            while not stop():
                data = port.read(max(1, port.in_waiting))  # waits at most read_timeout_s
                if data:
                    bridge.feed(data)
                bridge.report_if_due()
        except port_errors as exc:
            log.warning("lost %s (%s); reopening", name, exc)
            missing_reported = True  # the next failed open is part of this outage
        finally:
            try:
                port.close()
            except port_errors:
                pass


def _serial():
    """pyserial, imported only when a port is opened, so the rest works and is tested without it."""
    import serial  # noqa: PLC0415 (lazy on purpose: not a dependency yet)
    if not hasattr(serial, "Serial"):  # the unrelated PyPI package also called `serial`
        raise ImportError("the installed `serial` module is not pyserial")
    return serial


def open_serial_port(port: str, baud: int, timeout_s: float):
    return _serial().Serial(port, baud, timeout=timeout_s)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.pressure_bridge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="serial port, e.g. COM5")
    parser.add_argument("--baud", type=int, help="override pressure.bridge.baud")
    parser.add_argument("--endpoint", help="bind here (default: transport.pressure_sub)")
    parser.add_argument("--log", metavar="FILE", help="also write every message published to FILE "
                                                      "(replay it with mock_pressure --replay FILE)")
    parser.add_argument("--echo", action="store_true", help="print every line read from the port")
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
    try:
        serial = _serial()
    except ImportError:
        print(PYSERIAL_MISSING, file=sys.stderr)
        return 3
    bridge_cfg = cfg.pressure.bridge
    baud = args.baud if args.baud is not None else bridge_cfg.baud
    if baud <= 0:
        log.error("--baud must be above 0")
        return 2

    try:
        publisher = PressurePublisher(args.endpoint or cfg.transport.pressure_sub, log_path=args.log)
    except (RuntimeError, OSError) as exc:
        log.error("%s", exc)
        return 1
    log.info("publishing topic '%s' on %s from %s%s", TOPIC_PRESSURE, publisher.endpoint, args.port,
             f", logging to {args.log}" if args.log else "")
    bridge = PressureBridge(cfg.pressure, publisher.publish, echo=(lambda line: print(line, flush=True))
                            if args.echo else None)
    run_cfg = dataclasses.replace(bridge_cfg, baud=baud)
    try:
        run_bridge(bridge, lambda: open_serial_port(args.port, baud, bridge_cfg.read_timeout_s), run_cfg,
                   args.port, port_errors=(OSError, serial.SerialException))
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()
    skipped = ", ".join(f"{reason} {n}" for reason, n in sorted(bridge.skipped.items())) or "none"
    print(f"published {bridge.published} messages; lines skipped: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
