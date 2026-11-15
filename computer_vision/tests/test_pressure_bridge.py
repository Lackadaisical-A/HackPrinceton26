import logging
import sys
import time
import types

import pytest

from paul_vision import clock
from paul_vision import mock_pressure as mock
from paul_vision import pressure_bridge as pb
from paul_vision.config import (PressureBridgeConfig, PressureCalibrationConfig, PressureConfig,
                                PressureMockConfig)
from paul_vision.messages import TOPIC_PRESSURE, validate_pressure
from paul_vision.pressure import PressurePublisher, PressureReading
from paul_vision.transport import Subscriber

ANY_PORT = "tcp://127.0.0.1:0"

CAL = PressureCalibrationConfig(raw_zero=[100.0, 100.0, 100.0, 100.0], raw_full=[3100.0, 3100.0, 3100.0, 1100.0])
BRIDGE = PressureBridgeConfig(baud=115200, read_timeout_s=0.05, reopen_s=0.5, report_s=10.0)
# Deliberately not the shipped values.
CFG = PressureConfig(combine="both_fingers", on_threshold=0.3, off_threshold=0.1, on_hold_s=0.2, off_hold_s=0.4,
                     stale_s=0.25, calibration=CAL,
                     mock=PressureMockConfig(rate_hz=50.0, ramp_s=0.2, contact_level=0.7), bridge=BRIDGE)


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_bridge(**kwargs):
    sent: list[dict] = []
    clock = kwargs.pop("now", FakeClock())
    bridge = pb.PressureBridge(CFG, sent.append, now=clock, **kwargs)
    return bridge, sent, clock


# --- one line ---

@pytest.mark.parametrize("text, values", [
    ("1840,1322,1795,1290", (1840, 1322, 1795, 1290)),
    ("1840,1322,1795,1290\r", (1840, 1322, 1795, 1290)),
    (" 1.5 , 2 ,3,4 ", (1.5, 2, 3, 4)),
    ("1,2,3,4,", (1, 2, 3, 4)),              # firmware that prints a comma after every value
    ("1 2 3 4", (1, 2, 3, 4)),
    ("\t1\t2  3\t4", (1, 2, 3, 4)),
    ("-5,0,1e3,+7", (-5, 0, 1000, 7)),
])
def test_parse_line(text, values):
    assert pb.parse_line(text) == pytest.approx(values)


@pytest.mark.parametrize("text, reason", [
    ("", pb.SKIP_BLANK),
    ("   \r", pb.SKIP_BLANK),
    ("lt,lb,rt,rb", pb.SKIP_NUMBER),         # a header
    ("w,x,y,z", pb.SKIP_NUMBER),             # the IMU firmware's header, if the wrong board is plugged in
    ("BNO085 ready", pb.SKIP_FIELDS),        # debug text
    ("Sensor init failed, check wiring", pb.SKIP_FIELDS),
    ("1,2,3", pb.SKIP_FIELDS),
    ("1,2,3,4,5", pb.SKIP_FIELDS),
    ("1,,2,3,4", pb.SKIP_FIELDS),            # an empty field is never dropped: the pads would shift
    ("1,2,3,4,,", pb.SKIP_FIELDS),
    (",1,2,3,4", pb.SKIP_FIELDS),
    ("1;2;3;4", pb.SKIP_FIELDS),
    ("1,2,3,", pb.SKIP_NUMBER),
    ("1,2,3,x", pb.SKIP_NUMBER),
    ("1,2,3,nan", pb.SKIP_NUMBER),
    ("1,2,3,inf", pb.SKIP_NUMBER),
    ("1,2,3,4�", pb.SKIP_NUMBER),        # bytes that were not UTF-8
    ("1,2,3,0x10", pb.SKIP_NUMBER),
])
def test_parse_line_rejects(text, reason):
    with pytest.raises(pb.SkippedLine) as caught:
        pb.parse_line(text)
    assert caught.value.reason == reason


# --- lines from bytes ---

def test_line_splitter_handles_crlf_and_lines_split_across_reads():
    splitter = pb.LineSplitter()
    assert splitter.feed(b"1,2,3,4\r\n5,6,") == ["1,2,3,4"]
    assert splitter.feed(b"7,8\r") == []
    assert splitter.feed(b"\n\n9,9,9,9\n") == ["5,6,7,8", "", "9,9,9,9"]


def test_line_splitter_discards_an_overlong_line_once():
    splitter = pb.LineSplitter(max_len=16)
    assert splitter.feed(b"x" * 20) == [None]
    assert splitter.feed(b"y" * 20) == []        # still the same line
    assert splitter.feed(b"zz\n1,2,3,4\n") == ["1,2,3,4"]
    assert splitter.feed(b"a" * 17 + b"\n") == [None]


def test_line_splitter_replaces_bytes_that_are_not_utf8():
    assert pb.LineSplitter().feed(b"1,2,\xff,4\n") == ["1,2,�,4"]


# --- the bridge: bytes in, messages out ---

def test_the_bridge_publishes_good_lines_and_counts_the_rest():
    bridge, sent, clock = make_bridge()
    bridge.port_opened()
    data = (b"0,1322,1795,1290\r\n"            # the first line after opening: maybe cut, always skipped
            b"\r\n"
            b"lt,lb,rt,rb\r\n"
            b"BNO085 ready\r\n"
            b"1600,1600,1600,600\r\n"
            b"1,2,3\r\n"
            b"1,2,3,x\r\n"
            b"9999,100,0,1100\r\n")
    assert bridge.feed(data) == 2
    assert [m["raw"] for m in sent] == [[1600, 1600, 1600, 600], [9999, 100, 0, 1100]]
    # Calibrated per pad (the last pad has a smaller range), clipped to 0..1.
    assert [m["norm"] for m in sent] == [[0.5, 0.5, 0.5, 0.5], [1.0, 0.0, 0.0, 1.0]]
    assert all(m["t"] == clock.t for m in sent)
    for m in sent:
        validate_pressure(m)
    assert bridge.published == 2
    assert bridge.skipped == {pb.SKIP_FIRST: 1, pb.SKIP_BLANK: 1, pb.SKIP_NUMBER: 2, pb.SKIP_FIELDS: 2}


def test_each_line_is_stamped_when_it_is_read():
    bridge, sent, clock = make_bridge()
    bridge.feed(b"junk\n")
    for i in range(3):
        clock.t = 2000.0 + i * 0.02
        bridge.feed(b"100,100,100,100\n")
    assert [m["t"] for m in sent] == pytest.approx([2000.0, 2000.02, 2000.04])


def test_reopening_drops_the_half_line_and_skips_the_first_line_again():
    bridge, sent, _ = make_bridge()
    bridge.feed(b"x\n1,1,1,1\n2,2,")              # unplugged mid-line
    bridge.port_opened()
    bridge.feed(b"3,3\n4,4,4,4\n5,5,5,5\n")         # if the half line were kept, "2,2,3,3" would look valid
    assert [m["raw"] for m in sent] == [[1, 1, 1, 1], [4, 4, 4, 4], [5, 5, 5, 5]]
    assert bridge.skipped == {pb.SKIP_FIRST: 2}


def test_a_cut_first_line_that_still_looks_valid_is_not_published():
    bridge, sent, _ = make_bridge()
    bridge.port_opened()
    bridge.feed(b"22,1795,1290,7\n1,2,3,4\n")  # the tail of "1322,1795,1290,7": four numbers, three of them wrong
    assert [m["raw"] for m in sent] == [[1, 2, 3, 4]]


def test_an_overlong_first_line_counts_as_the_cut_one():
    bridge, sent, _ = make_bridge()
    bridge.feed(b"#" * 300 + b"\n1,2,3,4\n")
    assert [m["raw"] for m in sent] == [[1, 2, 3, 4]]
    assert bridge.skipped == {pb.SKIP_TOO_LONG: 1}


def test_echo_sees_every_line():
    seen = []
    bridge, _, _ = make_bridge(echo=seen.append)
    bridge.feed(b"hello\n1,2,3,4\n")
    assert seen == ["hello", "1,2,3,4"]


def test_skips_are_logged_once_per_reason_and_then_reported_periodically(caplog):
    caplog.set_level(logging.INFO, logger="paul_vision.pressure_bridge")
    bridge, _, clock = make_bridge()
    bridge.feed(b"first\n" + b"header,a,b,c\n" * 50 + b"1,2,3,4\n" * 10)
    assert caplog.text.count("skipped a line") == 1
    bridge.report_if_due()
    assert "published" not in caplog.text  # not due yet
    clock.t += BRIDGE.report_s
    bridge.report_if_due()
    assert "published 10 samples in 10 s (1.0/s); skipped: first line after opening 1, not a number 50" in caplog.text


def test_the_report_says_when_nothing_arrives(caplog):
    caplog.set_level(logging.INFO, logger="paul_vision.pressure_bridge")
    bridge, _, clock = make_bridge()
    clock.t += BRIDGE.report_s
    bridge.report_if_due()
    assert "no samples in the last 10 s (nothing received)" in caplog.text


# --- the read loop: a port that disappears and comes back ---

class FakePort:
    def __init__(self, chunks, then=OSError("device disconnected")):
        self._chunks = list(chunks)
        self._then = then
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, n: int) -> bytes:
        if not self._chunks:
            raise self._then
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True


def test_run_bridge_survives_the_port_disappearing_and_coming_back(caplog):
    caplog.set_level(logging.INFO, logger="paul_vision.pressure_bridge")
    bridge, sent, _ = make_bridge()
    ports = [FakePort([b"cut\n1,1,1,1\n", b"2,2,2,2\n"]), FakePort([b"cut\n3,3,3,3\n"])]
    plan = [OSError("could not open port 'COM5'"), OSError("could not open port 'COM5'"), ports[0],
            OSError("could not open port 'COM5'"), ports[1]]
    sleeps = []

    def open_port():
        item = plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    pb.run_bridge(bridge, open_port, BRIDGE, "COM5", stop=lambda: len(sent) >= 3, sleep=sleeps.append)
    assert [m["raw"] for m in sent] == [[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]]
    assert plan == []
    assert sleeps == [BRIDGE.reopen_s] * 3
    assert ports[0].closed and ports[1].closed
    assert caplog.text.count("cannot open COM5") == 1  # once before the first open; the later failure is "lost"
    assert caplog.text.count("lost COM5") == 1
    assert caplog.text.count("COM5 open at 115200 baud") == 2
    assert bridge.skipped == {pb.SKIP_FIRST: 2}


def test_run_bridge_stops_when_asked():
    bridge, _, _ = make_bridge()
    calls = []
    pb.run_bridge(bridge, lambda: calls.append(1), BRIDGE, "COM5", stop=lambda: True)
    assert calls == []


# --- pyserial is optional ---

def test_a_missing_pyserial_is_one_line_naming_the_fix(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "serial", None)
    assert pb.main(["--port", "COM5", "--endpoint", ANY_PORT]) == 3
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1
    assert "pyserial" in err and "uv add pyserial" in err


def test_an_unrelated_serial_module_is_not_mistaken_for_pyserial(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", types.ModuleType("serial"))
    with pytest.raises(ImportError, match="not pyserial"):
        pb.open_serial_port("COM5", 115200, 0.05)


def test_the_port_is_opened_with_the_configured_settings(monkeypatch):
    opened = {}
    fake = types.ModuleType("serial")
    fake.Serial = lambda port, baud, timeout: opened.update(port=port, baud=baud, timeout=timeout) or "port"
    fake.SerialException = OSError
    monkeypatch.setitem(sys.modules, "serial", fake)
    assert pb.open_serial_port("COM7", 115200, 0.05) == "port"
    assert opened == {"port": "COM7", "baud": 115200, "timeout": 0.05}


def test_main_runs_the_bridge_with_a_fake_pyserial(monkeypatch, tmp_path, capsys):
    """Everything main does except reading a real port: config, bind, open, publish, log, Ctrl+C."""
    fake = types.ModuleType("serial")
    fake.SerialException = type("SerialException", (OSError,), {})
    port = FakePort([b"cut\n100,100,100,100\n", b"3100,3100,3100,1100\n"], then=KeyboardInterrupt())
    fake.Serial = lambda name, baud, timeout: port
    monkeypatch.setitem(sys.modules, "serial", fake)
    log_path = tmp_path / "bridge.log"
    assert pb.main(["--port", "COM5", "--endpoint", ANY_PORT, "--log", str(log_path)]) == 0
    with open(log_path, encoding="utf-8") as fh:
        readings, skipped = mock.read_log(fh)
    assert skipped == 0 and [r.raw for r in readings] == [(100.0,) * 4, (3100.0, 3100.0, 3100.0, 1100.0)]
    assert "published 2 messages" in capsys.readouterr().out


# --- a bridge log, replayed by the mock ---

def test_a_log_written_by_the_bridge_is_replayed_by_the_mock(tmp_path):
    log_path = tmp_path / "bridge.log"
    fake_now = FakeClock(5000.0)
    with PressurePublisher(ANY_PORT, log_path=log_path) as bridge_pub:
        bridge = pb.PressureBridge(CFG, bridge_pub.publish, now=fake_now)
        bridge.port_opened()
        bridge.feed(b"cut\r\n")
        for i in range(6):
            fake_now.t = 5000.0 + i * 0.04
            bridge.feed(f"{100 + i * 500},{100 + i * 400},{100 + i * 300},{1100 - i * 100}\r\n".encode())
            if i == 2:
                bridge.feed(b"debug: tare done\r\n")  # skipped, so never in the log
    with open(log_path, encoding="utf-8") as fh:
        logged, skipped = mock.read_log(fh)
    assert skipped == 0 and len(logged) == 6

    with PressurePublisher(ANY_PORT) as mock_pub, Subscriber(mock_pub.endpoint, topic=TOPIC_PRESSURE) as sub:
        deadline = time.monotonic() + 5
        while sub.recv(0.02) is None:  # wait out the PUB/SUB slow joiner
            assert time.monotonic() < deadline, "the subscriber never connected"
            mock_pub.publish({"t": 1.0, "raw": [0] * 4, "norm": [0] * 4})
        time.sleep(0.1)
        while sub.recv(0) is not None:
            pass
        before = clock.now()
        assert mock.replay(mock_pub, logged) == 6
        replayed = [PressureReading.from_message(sub.recv(1.0)[1]) for _ in range(6)]
    assert [r.raw for r in replayed] == [r.raw for r in logged]
    assert [r.norm for r in replayed] == [r.norm for r in logged]
    gaps = [b.t - a.t for a, b in zip(replayed, replayed[1:])]
    assert gaps == pytest.approx([0.04] * 5, abs=2e-4)  # the bridge's timing...
    assert replayed[0].t >= before - 0.001             # ...on today's clock
