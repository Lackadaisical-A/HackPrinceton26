import io
import logging
import random
import sys
import threading
import time

import pytest
import yaml

from paul_vision import clock
from paul_vision import mock_pressure as mock
from paul_vision.config import (DEFAULT_CONFIG_PATH, PressureBridgeConfig, PressureCalibrationConfig, PressureConfig,
                                PressureMockConfig)
from paul_vision.messages import TOPIC_PRESSURE, build_pressure_message, encode
from paul_vision.pressure import ContactDetector, PressurePublisher, PressureReading, PressureReceiver, denormalize
from paul_vision.transport import Subscriber

ANY_PORT = "tcp://127.0.0.1:0"

CAL = PressureCalibrationConfig(raw_zero=[100.0] * 4, raw_full=[3100.0] * 4)
# Deliberately not the shipped values.
CFG = PressureConfig(combine="both_fingers", on_threshold=0.3, off_threshold=0.1, on_hold_s=0.2, off_hold_s=0.4,
                     stale_s=0.25, calibration=CAL,
                     mock=PressureMockConfig(rate_hz=50.0, ramp_s=0.2, contact_level=0.7),
                     bridge=PressureBridgeConfig(baud=115200, read_timeout_s=0.05, reopen_s=1.0, report_s=10.0))
FAST = PressureConfig(combine="both_fingers", on_threshold=0.3, off_threshold=0.1, on_hold_s=0.1, off_hold_s=0.1,
                      stale_s=0.25, calibration=CAL, mock=CFG.mock, bridge=CFG.bridge)


# --- script parsing and levels ---

@pytest.mark.parametrize("text, steps", [
    ("0:0,1:0.6,3:0.6,3.5:0", [(0.0, 0.0), (1.0, 0.6), (3.0, 0.6), (3.5, 0.0)]),
    ("0:0 1:0.6", [(0.0, 0.0), (1.0, 0.6)]),
    ("  0.5:1 ,\n 2:0  ", [(0.5, 1.0), (2.0, 0.0)]),
])
def test_parse_script(text, steps):
    assert mock.parse_script(text) == steps


@pytest.mark.parametrize("text, problem", [
    ("", "empty"),
    ("  , ", "empty"),
    ("1", "not time:level"),
    ("a:0.5", "not time:level"),
    ("1:x", "not time:level"),
    ("1:1.5", "level must be in 0..1"),
    ("1:-0.1", "level must be in 0..1"),
    ("1:nan", "level must be in 0..1"),
    ("-1:0", "time must be 0 or more"),
    ("inf:0", "time must be 0 or more"),
    ("1:0,1:0.5", "times must increase"),
    ("2:0,1:0.5", "times must increase"),
])
def test_parse_script_rejects(text, problem):
    with pytest.raises(ValueError, match=problem):
        mock.parse_script(text)


def test_a_ramp_is_linear_and_starts_from_where_the_level_is():
    ramp = mock.Ramp(0.2)
    ramp.set_target(0.6, 1.0)
    assert [ramp.value(t) for t in (0.5, 1.0, 1.1, 1.2, 9.0)] == pytest.approx([0.0, 0.0, 0.3, 0.6, 0.6])
    ramp.set_target(0.0, 1.1)  # changed mid-ramp, at 0.3
    assert [ramp.value(t) for t in (1.1, 1.2, 1.3, 2.0)] == pytest.approx([0.3, 0.15, 0.0, 0.0])
    assert ramp.target == 0.0


def test_a_zero_ramp_steps():
    ramp = mock.Ramp(0.0)
    ramp.set_target(0.8, 1.0)
    assert ramp.value(1.0) == 0.8


def test_script_levels():
    script = mock.ScriptLevels(mock.parse_script("0:0,1:0.6,3:0.6,3.5:0"), ramp_s=0.2)
    times = (0.5, 1.0, 1.1, 1.2, 2.0, 3.2, 3.6, 3.7, 10.0)
    assert [script(t) for t in times] == pytest.approx([0.0, 0.0, 0.3, 0.6, 0.6, 0.6, 0.3, 0.0, 0.0])
    assert script.end_t == pytest.approx(3.7)


def test_a_script_starts_at_level_zero():
    script = mock.ScriptLevels([(1.0, 0.5)], ramp_s=0.0)
    assert script(0.5) == 0.0 and script(1.0) == 0.5


def test_level_message_puts_the_level_on_every_pad():
    msg = mock.level_message(0.5, 1731542400.0, CAL)
    assert msg["norm"] == [0.5] * 4 and msg["raw"] == [1600] * 4
    assert mock.level_message(1.3, 1.0, CAL)["norm"] == [1.0] * 4  # clipped, never an invalid message


def test_the_script_mode_makes_the_detector_report_on_then_off():
    """The level curve of a script, sampled at the mock's rate, through the real message and the detector."""
    script = mock.ScriptLevels(mock.parse_script("0:0,1:0.6,3:0.6,3.5:0"), CFG.mock.ramp_s)
    detector = ContactDetector(CFG)
    changes, previous = [], "start"
    for n in range(int(5.0 * CFG.mock.rate_hz)):
        t = round(n / CFG.mock.rate_hz, 6)
        reading = PressureReading.from_message(mock.level_message(script(t), t, CFG.calibration))
        state = detector.update(reading, t).state
        if state != previous:
            changes.append((state, t))
            previous = state
    assert [s for s, _ in changes] == [None, False, True, False]
    _, t_false, t_on, t_off = (t for _, t in changes)
    sample = 1 / CFG.mock.rate_hz
    assert t_false == pytest.approx(CFG.off_hold_s)
    # The ramp crosses on_threshold at 1.0 + 0.2 * 0.3 / 0.6 = 1.1 s; then on_hold_s.
    assert 1.1 + CFG.on_hold_s - 1e-6 <= t_on <= 1.1 + CFG.on_hold_s + sample
    # Down from 0.6 at 3.5 s, it crosses off_threshold at 3.5 + 0.2 * 0.5 / 0.6 = 3.667 s; then off_hold_s.
    assert 3.5 + 0.2 * 0.5 / 0.6 + CFG.off_hold_s - 1e-6 <= t_off <= 3.5 + 0.2 * 0.5 / 0.6 + CFG.off_hold_s + sample


# --- keys ---

@pytest.mark.parametrize("key, target, expected", [
    (" ", 0.0, 0.7),
    (" ", 0.7, 0.0),
    (" ", 0.3, 0.0),   # any level counts as contact for the toggle
    ("0", 0.7, 0.0),
    ("5", 0.0, 0.5),
    ("9", 0.0, 0.9),
    ("x", 0.4, None),
    ("\r", 0.4, None),
])
def test_key_target(key, target, expected):
    assert mock.key_target(key, target, contact_level=0.7) == expected


@pytest.mark.parametrize("key", ["q", "Q", "\x1b", "\x03"])
def test_quit_keys(key):
    assert mock.key_target(key, 0.0, 0.7) is mock.QUIT


def test_no_keyboard_without_msvcrt(monkeypatch):
    monkeypatch.setitem(sys.modules, "msvcrt", None)  # as on Linux or macOS
    assert mock._console_keys() is None


def test_no_keyboard_when_stdin_is_not_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert mock._console_keys() is None


# --- replay ---

def _line(t: float, level: float) -> str:
    return encode(TOPIC_PRESSURE, build_pressure_message(t=t, raw=(level * 1000,) * 4, norm=(level,) * 4)).decode()


def test_read_log_takes_pressure_lines_and_skips_the_rest():
    lines = [
        "subscribed to 'pressure' on tcp://127.0.0.1:5556  (Ctrl+C to stop)",  # subscribe_print --raw
        _line(10.0, 0.1),
        "",
        'vision {"t":1}',
        "pressure {not json",
        'pressure {"t":10.01,"raw":[1,2,3,4],"norm":[0.1,0.2,0.3,1.5]}',
        _line(10.02, 0.2) + "\n",
        "2 messages in 0.1 s (20.0/s), ...",
    ]
    readings, skipped = mock.read_log(lines)
    assert [r.t for r in readings] == [10.0, 10.02]
    assert readings[1].norm == (0.2,) * 4
    assert skipped == 5


def test_retime_keeps_the_spacing_and_never_goes_back():
    readings = [PressureReading(t=t, raw=(0.0,) * 4, norm=(0.0,) * 4) for t in (10.0, 10.02, 10.01, 10.05)]
    assert [t for t, _ in mock.retime(readings, 100.0)] == pytest.approx([100.0, 100.02, 100.02, 100.06])


def connect(pub: PressurePublisher, sub: Subscriber) -> None:
    deadline = time.monotonic() + 5
    while sub.recv(0.02) is None:
        assert time.monotonic() < deadline, "the subscriber never connected"
        pub.publish(build_pressure_message(t=1.0, raw=(0,) * 4, norm=(0,) * 4))
    time.sleep(0.1)
    while sub.recv(0) is not None:
        pass


def test_replay_keeps_the_original_timing_over_zmq():
    original = [PressureReading(t=50.0 + dt, raw=(float(i),) * 4, norm=(i / 10,) * 4)
                for i, dt in enumerate((0.0, 0.05, 0.10, 0.30))]
    with PressurePublisher(ANY_PORT) as pub, Subscriber(pub.endpoint, topic=TOPIC_PRESSURE) as sub:
        connect(pub, sub)
        before, started = clock.now(), time.perf_counter()
        assert mock.replay(pub, original) == 4
        took = time.perf_counter() - started
        got = [sub.recv(1.0)[1] for _ in range(4)]
    assert 0.29 <= took < 0.45
    assert [m["raw"] for m in got] == [[0] * 4, [1] * 4, [2] * 4, [3] * 4]
    ts = [m["t"] for m in got]
    assert ts[0] >= before - 0.001  # restamped on today's clock, not 50.0
    assert [b - a for a, b in zip(ts, ts[1:])] == pytest.approx([0.05, 0.05, 0.2], abs=2e-4)


def test_replay_loops_until_stopped():
    original = [PressureReading(t=float(i) * 0.01, raw=(float(i),) * 4, norm=(0.0,) * 4) for i in range(3)]
    with PressurePublisher(ANY_PORT) as pub:
        sent = mock.replay(pub, original, loop=True, gap_s=0.01, stop=lambda: pub.sent >= 10)
    assert sent == 10


# --- publishing levels ---

def test_run_levels_over_zmq_turns_contact_on_then_off_at_the_set_rate():
    script = mock.ScriptLevels(mock.parse_script("0:0,0.4:0.8,0.9:0"), ramp_s=0.05)
    rate_hz, duration = 50.0, script.end_t + 0.2
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        deadline = time.monotonic() + 5
        while receiver.poll() is None:  # wait out the PUB/SUB slow joiner
            assert time.monotonic() < deadline
            pub.publish(mock.level_message(0.0, clock.now(), CAL))
            time.sleep(0.02)
        before = pub.sent
        result = {}
        thread = threading.Thread(target=lambda: result.update(sent=mock.run_levels(
            pub, script, rate_hz, CAL, duration=duration)))
        detector, states = ContactDetector(FAST), []
        started = time.perf_counter()
        thread.start()
        while thread.is_alive() or time.perf_counter() - started < duration + 0.4:
            contact = detector.update(receiver.poll(), clock.now())
            if not states or states[-1] != contact.state:
                states.append(contact.state)
            time.sleep(0.01)
        thread.join()
    assert states[-4:] == [False, True, False, None]  # None: the mock stopped, so the data went stale
    assert pub.sent - before == result["sent"]
    assert 0.85 * rate_hz * duration <= result["sent"] <= rate_hz * duration + 2


class FakeTime:
    """Stands in for the `time` module inside mock_pressure: perf_counter() and sleep() on a fake clock."""

    def __init__(self):
        self.now = 1000.0

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class LatePublisher:
    """Records when (on the fake clock) each message is sent; sending message i takes late[i] seconds, as a slow
    log write or a descheduled thread would. Binds nothing; can stand in for PressurePublisher in main."""

    endpoint = "fake"

    def __init__(self, fake: FakeTime, late: dict[int, float] | None = None):
        self.fake, self.late, self.times, self.levels, self.stamps = fake, late or {}, [], [], []

    @property
    def sent(self) -> int:
        return len(self.times)

    def publish(self, msg: dict) -> None:
        self.times.append(self.fake.now)
        self.levels.append(msg["norm"][0])
        self.stamps.append(msg["t"])
        self.fake.now += self.late.get(len(self.times) - 1, 0.0)

    def close(self) -> None:
        pass


def run_on_a_fake_clock(monkeypatch, level_at, rate_hz, duration, late=None, stop=lambda: False):
    """run_levels with no real sleeping: returns (sent, the scheduled times level_at was asked for, publisher)."""
    fake = FakeTime()
    monkeypatch.setattr(mock, "time", fake)
    pub, ticks = LatePublisher(fake, late), []

    def recording(t):
        ticks.append(t)
        return level_at(t)

    sent = mock.run_levels(pub, recording, rate_hz, CAL, duration=duration, stop=stop)
    return sent, ticks, pub


def assert_ends_with_the_sample_at(duration, sent, ticks, pub):
    assert sent == len(ticks) == len(pub.times)
    assert ticks[-1] == duration  # exactly, and last
    assert sum(t >= duration - 1e-9 for t in ticks) == 1  # once
    assert all(t <= duration for t in ticks)  # nothing past it
    assert all(b > a for a, b in zip(ticks, ticks[1:]))
    assert all(b > a for a, b in zip(pub.times, pub.times[1:]))  # no two sent at the same instant: no burst


# The script of test_main_script_mode_publishes_the_curve_and_logs_it: 0.3 s at 100 Hz, ending on 0.8.
@pytest.mark.parametrize("late", [
    {},
    {29: 0.015},                                 # the sample at 0.29 is 15 ms late: the next would be at 0.305
    {28: 0.05},                                  # late past the end
    {5: 0.025},                                  # late early on, so later samples are off the period's grid
    {i: 0.013 for i in range(0, 40, 3)},         # late again and again
], ids=["on time", "late before the end", "late past the end", "late early", "late often"])
def test_run_levels_always_ends_with_the_sample_at_duration(monkeypatch, late):
    script = mock.ScriptLevels(mock.parse_script("0:0,0.2:0.8"), ramp_s=0.1)
    sent, ticks, pub = run_on_a_fake_clock(monkeypatch, script, 100.0, script.end_t, late)
    assert_ends_with_the_sample_at(script.end_t, sent, ticks, pub)
    assert pub.levels[0] == 0.0 and pub.levels[-1] == 0.8
    assert pub.levels == sorted(pub.levels)
    assert sent <= 31
    if not late:
        assert sent == 31 and ticks[:-1] == pytest.approx([n / 100 for n in range(30)])


@pytest.mark.parametrize("steps, ramp_s, rate_hz, duration", [
    ("0:0,0.1:0.8", 0.05, 100.0, 0.155),
    ("0:0,1.23:0.6", 0.2, 50.0, None),           # end_t 1.43: not a whole number of 0.02 s periods
])
def test_run_levels_ends_at_duration_also_between_two_periods(monkeypatch, steps, ramp_s, rate_hz, duration):
    script = mock.ScriptLevels(mock.parse_script(steps), ramp_s)
    duration = script.end_t if duration is None else duration
    sent, ticks, pub = run_on_a_fake_clock(monkeypatch, script, rate_hz, duration)
    assert_ends_with_the_sample_at(duration, sent, ticks, pub)
    assert ticks[-2] == pytest.approx((sent - 2) / rate_hz)  # the grid up to there
    assert pub.levels[-1] == round(script(duration), 3)


@pytest.mark.parametrize("seed", range(40))
def test_run_levels_ends_at_duration_whatever_the_hiccups(monkeypatch, seed):
    rng = random.Random(seed)
    late = {i: rng.choice([0.0, 0.0, 0.0, 0.002, 0.008, 0.012, 0.03, 0.2]) for i in range(200)}
    script = mock.ScriptLevels(mock.parse_script("0:0,0.4:0.8,0.9:0"), ramp_s=0.05)
    duration = rng.choice([script.end_t, 1.0, 1.013, 0.5])
    sent, ticks, pub = run_on_a_fake_clock(monkeypatch, script, 50.0, duration, late)
    assert_ends_with_the_sample_at(duration, sent, ticks, pub)
    assert pub.levels[-1] == round(script(duration), 3)


def test_stop_or_no_level_ends_run_levels_at_once_without_the_sample_at_duration(monkeypatch):
    script = mock.ScriptLevels(mock.parse_script("0:0,0.2:0.8"), ramp_s=0.1)
    count = []
    sent, ticks, _ = run_on_a_fake_clock(monkeypatch, lambda t: count.append(t) or script(t), 100.0, script.end_t,
                                         stop=lambda: len(count) >= 5)
    assert sent == 5 and ticks == pytest.approx([0.0, 0.01, 0.02, 0.03, 0.04])
    sent, ticks, _ = run_on_a_fake_clock(monkeypatch, lambda t: None if t > 0.095 else script(t), 100.0,
                                         script.end_t)
    assert sent == 10 and ticks[-1] == pytest.approx(0.1)  # asked at 0.1, which ended it; 0.0 to 0.09 sent
    assert run_on_a_fake_clock(monkeypatch, script, 100.0, -1.0)[0] == 0


# --- the command line ---

@pytest.fixture
def config_path(tmp_path):
    """The shipped config with this file's own pressure section, so tuning config.yaml never breaks these tests."""
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["pressure"] = {
        "combine": "both_fingers", "on_threshold": 0.3, "off_threshold": 0.1, "on_hold_s": 0.1, "off_hold_s": 0.1,
        "stale_s": 0.25, "calibration": {"raw_zero": [100] * 4, "raw_full": [3100] * 4},
        "mock": {"rate_hz": 100, "ramp_s": 0.1, "contact_level": 0.7},
        "bridge": {"baud": 115200, "read_timeout_s": 0.05, "reopen_s": 1.0, "report_s": 10.0},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def levels_in(path) -> list[float]:
    with open(path, encoding="utf-8") as fh:
        readings, skipped = mock.read_log(fh)
    assert skipped == 0
    return [r.norm[0] for r in readings]


def test_main_script_mode_publishes_the_curve_and_logs_it(config_path, tmp_path, capsys):
    log_path = tmp_path / "out.log"
    assert mock.main(["--script", "0:0,0.2:0.8", "--endpoint", ANY_PORT, "--log", str(log_path),
                      "--config", config_path]) == 0
    levels = levels_in(log_path)
    assert 25 <= len(levels) <= 32  # 0.3 s (last step + ramp_s) at 100 Hz
    assert levels[0] == 0.0 and levels[-1] == 0.8
    assert levels == sorted(levels)  # a ramp, not a jump
    assert any(0.0 < v < 0.8 for v in levels)
    assert "published" in capsys.readouterr().out


def test_main_replays_a_log(config_path, tmp_path):
    source = tmp_path / "in.log"
    source.write_text("\n".join(_line(10.0 + i * 0.01, i / 10) for i in range(8)) + "\n", encoding="utf-8")
    out = tmp_path / "out.log"
    assert mock.main(["--replay", str(source), "--endpoint", ANY_PORT, "--log", str(out),
                      "--config", config_path]) == 0
    assert levels_in(out) == pytest.approx([i / 10 for i in range(8)])


def replay_on_a_fake_clock(monkeypatch, config_path, tmp_path, log, args, late=None):
    """main --replay with no real sleeping and nothing bound: returns (publisher, seconds the run took)."""
    source = tmp_path / "in.log"
    source.write_text("\n".join(_line(t, level) for t, level in log) + "\n", encoding="utf-8")
    fake = FakeTime()
    monkeypatch.setattr(mock, "time", fake)
    pub = LatePublisher(fake, late)
    monkeypatch.setattr(mock, "PressurePublisher", lambda endpoint, log_path=None: pub)
    started = fake.now
    assert mock.main(["--replay", str(source)] + args + ["--endpoint", ANY_PORT, "--config", config_path]) == 0
    return pub, fake.now - started


PAUSED_LOG = [(10.0, 0.1), (10.1, 0.2), (12.0, 0.3), (12.1, 0.4)]  # a 1.9 s pause after 0.1 s
SHORT_LOG = [(10.0, 0.1), (10.01, 0.2), (10.02, 0.3)]


@pytest.mark.parametrize("log, args, late, levels, took", [
    (PAUSED_LOG, ["--duration", "0.5"], None, [0.1, 0.2], 0.5),   # the next reading is due after 0.5 s
    (PAUSED_LOG, ["--duration", "0.05"], None, [0.1], 0.05),
    (PAUSED_LOG, ["--duration", "0.5"], {0: 0.6}, [0.1], 0.6),    # held up past the end: ends at once
    (SHORT_LOG, ["--loop", "--duration", "0.105"], None, [0.1, 0.2, 0.3] * 3 + [0.1, 0.2], 0.105),
    (SHORT_LOG, ["--loop", "--rate", "2", "--duration", "0.1"], None, [0.1, 0.2, 0.3], 0.1),  # inside the 0.5 s gap
], ids=["pause after the end", "pause, shorter run", "late past the end", "loop", "loop, gap after the end"])
def test_main_replay_sends_nothing_due_after_the_duration_and_ends_then(monkeypatch, config_path, tmp_path,
                                                                        log, args, late, levels, took):
    pub, run_s = replay_on_a_fake_clock(monkeypatch, config_path, tmp_path, log, args, late)
    duration = float(args[args.index("--duration") + 1])
    assert pub.levels == pytest.approx(levels)
    assert all(t - 1000.0 <= duration + 1e-9 for t in pub.times)  # nothing sent after the end
    assert run_s == pytest.approx(took, abs=1e-6)


def test_main_replay_without_a_duration_sends_the_whole_log_with_its_timing(monkeypatch, config_path, tmp_path):
    pub, run_s = replay_on_a_fake_clock(monkeypatch, config_path, tmp_path, PAUSED_LOG, [])
    assert pub.levels == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert [t - 1000.0 for t in pub.times] == pytest.approx([0.0, 0.1, 2.0, 2.1], abs=1e-6)
    assert [b - a for a, b in zip(pub.stamps, pub.stamps[1:])] == pytest.approx([0.1, 1.9, 0.1], abs=2e-4)
    assert run_s == pytest.approx(2.1, abs=1e-6)


def test_replay_sends_readings_that_fell_due_during_a_hiccup_at_once(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(mock, "time", fake)
    pub = LatePublisher(fake, late={0: 0.25})
    readings = [PressureReading(t=10.0 + i * 0.1, raw=(0.0,) * 4, norm=(i / 10,) * 4) for i in range(5)]
    assert mock.replay(pub, readings) == 5
    assert [t - 1000.0 for t in pub.times] == pytest.approx([0.0, 0.25, 0.25, 0.3, 0.4], abs=1e-6)  # deliberate
    assert [b - a for a, b in zip(pub.stamps, pub.stamps[1:])] == pytest.approx([0.1] * 4, abs=2e-4)


def test_main_key_mode(monkeypatch, config_path, tmp_path):
    presses = iter([[" "]] + [[]] * 30 + [["q"]])
    monkeypatch.setattr(mock, "_console_keys", lambda: lambda: next(presses))
    out = tmp_path / "out.log"
    assert mock.main(["--endpoint", ANY_PORT, "--log", str(out), "--config", config_path]) == 0
    levels = levels_in(out)
    assert levels[-1] == 0.7  # contact_level, reached by a ramp
    assert any(0.0 < v < 0.7 for v in levels)


def test_main_without_a_keyboard_publishes_level_zero(monkeypatch, config_path, tmp_path, caplog):
    monkeypatch.setattr(mock, "_console_keys", lambda: None)
    caplog.set_level(logging.WARNING)
    out = tmp_path / "out.log"
    assert mock.main(["--duration", "0.15", "--endpoint", ANY_PORT, "--log", str(out), "--config", config_path]) == 0
    assert "no console keyboard" in caplog.text
    levels = levels_in(out)
    assert levels and set(levels) == {0.0}


@pytest.mark.parametrize("args", [
    ["--script", "1:2"],
    ["--script", "0:0", "--rate", "0"],
    ["--loop"],
    ["--replay", "does-not-exist.log"],
])
def test_main_refuses_bad_arguments(args, config_path):
    assert mock.main(args + ["--endpoint", ANY_PORT, "--config", config_path]) == 2


def test_main_refuses_a_log_without_pressure_messages(config_path, tmp_path):
    path = tmp_path / "empty.log"
    path.write_text('vision {"t":1}\n', encoding="utf-8")
    assert mock.main(["--replay", str(path), "--endpoint", ANY_PORT, "--config", config_path]) == 2


def test_main_reports_a_port_in_use(config_path):
    with PressurePublisher(ANY_PORT) as pub:
        assert mock.main(["--script", "0:0", "--endpoint", pub.endpoint, "--config", config_path]) == 1


def test_mock_raw_values_follow_the_calibration():
    msg = mock.level_message(0.25, 1.0, CAL)
    assert msg["raw"] == [round(v) for v in denormalize((0.25,) * 4, CAL)]
