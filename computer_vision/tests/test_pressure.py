import copy
import dataclasses
import logging
import math
import re
import socket
import time

import pytest
import yaml

from paul_vision import clock
from paul_vision import config as config_module
from paul_vision.config import (DEFAULT_CONFIG_PATH, ConfigError, PressureBridgeConfig, PressureCalibrationConfig,
                                PressureConfig, PressureMockConfig, from_dict, validate_pressure_config)
from paul_vision.messages import (PRESSURE_PADS, TOPIC_PRESSURE, build_pressure_message, decode, encode,
                                  validate_pressure)
from paul_vision.pressure import (COMBINE_RULES, Contact, ContactDetector, PressurePublisher, PressureReading,
                                  PressureReceiver, denormalize, grip_level, normalize)

ANY_PORT = "tcp://127.0.0.1:0"

CAL = PressureCalibrationConfig(raw_zero=[100.0, 100.0, 100.0, 100.0], raw_full=[3100.0, 3100.0, 3100.0, 3100.0])
MOCK = PressureMockConfig(rate_hz=50.0, ramp_s=0.1, contact_level=0.7)
BRIDGE = PressureBridgeConfig(baud=115200, read_timeout_s=0.05, reopen_s=1.0, report_s=10.0)
# Deliberately not the shipped values: tuning config.yaml must never break these tests.
CFG = PressureConfig(combine="both_fingers", on_threshold=0.3, off_threshold=0.1, on_hold_s=0.2, off_hold_s=0.4,
                     stale_s=0.25, calibration=CAL, mock=MOCK, bridge=BRIDGE)
DT = 0.02  # 50 Hz


def reading(t: float, level: float | tuple = 0.0) -> PressureReading:
    norm = level if isinstance(level, tuple) else (level,) * 4
    return PressureReading(t=t, raw=(0.0,) * 4, norm=norm)


def feed(detector: ContactDetector, start: float, end: float, level, dt: float = DT) -> list[tuple[float, Contact]]:
    """Samples from `start` up to (not including) `end`, each passed to update() at its own sample time."""
    out, n = [], 0
    while start + n * dt < end - 1e-9:
        t = round(start + n * dt, 6)
        out.append((t, detector.update(reading(t, level), t)))
        n += 1
    return out


def first_time(trace, state):
    return next((t for t, c in trace if c.state is state), None)


def settled(state: bool, cfg: PressureConfig = CFG) -> tuple[ContactDetector, float]:
    """A detector that has decided `state`, and the time of its last sample."""
    detector = ContactDetector(cfg)
    trace = feed(detector, 0.0, 1.0, 0.0)
    if state:
        trace = feed(detector, 1.0, 2.0, 0.6)
    assert trace[-1][1].state is state
    return detector, trace[-1][0]


# --- contact: hysteresis and debounce ---

def test_unknown_before_any_reading():
    detector = ContactDetector(CFG)
    contact = detector.update(None, 10.0)
    assert contact == Contact(state=None, level=None, since=10.0)
    assert not contact.fresh and not contact.holding


def test_not_holding_needs_the_off_hold_time_too():
    detector = ContactDetector(CFG)
    trace = feed(detector, 0.0, 1.0, 0.0)
    assert trace[0][1].state is None and trace[0][1].fresh  # fresh, but not decided yet
    t_off = first_time(trace, False)
    assert t_off == pytest.approx(CFG.off_hold_s)
    assert dict(trace)[t_off].since == t_off


def test_contact_turns_on_after_the_on_hold_time():
    detector, _ = settled(False)
    trace = feed(detector, 1.0, 2.0, 0.6)
    t_on = first_time(trace, True)
    assert t_on == pytest.approx(1.0 + CFG.on_hold_s)
    assert all(c.state is False for t, c in trace if t < t_on)
    on = dict(trace)[t_on]
    assert on.holding and on.since == t_on and on.level == pytest.approx(0.6)


def test_contact_turns_off_after_the_off_hold_time():
    detector, _ = settled(True)
    trace = feed(detector, 2.0, 3.0, 0.0)
    t_off = first_time(trace, False)
    assert t_off == pytest.approx(2.0 + CFG.off_hold_s)
    assert all(c.state is True for t, c in trace if t < t_off)


def test_the_thresholds_themselves_count():
    detector, _ = settled(False)
    assert first_time(feed(detector, 1.0, 2.0, CFG.on_threshold), True) == pytest.approx(1.0 + CFG.on_hold_s)
    assert first_time(feed(detector, 2.0, 3.0, CFG.off_threshold), False) == pytest.approx(2.0 + CFG.off_hold_s)


@pytest.mark.parametrize("state", [True, False])
def test_a_level_between_the_thresholds_keeps_the_state(state):
    detector, t = settled(state)
    trace = feed(detector, t + DT, t + 5.0, (CFG.on_threshold + CFG.off_threshold) / 2)
    assert all(c.state is state for _, c in trace)


def test_a_spike_shorter_than_the_on_hold_is_not_a_grasp():
    detector, _ = settled(False)
    trace = feed(detector, 1.0, 1.0 + CFG.on_hold_s - 0.05, 0.9)  # a bump
    trace += feed(detector, 1.15, 3.0, 0.0)
    assert all(c.state is False for _, c in trace)


def test_a_dip_shorter_than_the_off_hold_is_not_a_release():
    detector, _ = settled(True)
    trace = feed(detector, 2.0, 2.0 + CFG.off_hold_s - 0.05, 0.0)  # the object shifts in the grip
    trace += feed(detector, 2.35, 4.0, 0.6)
    assert all(c.state is True for _, c in trace)


def test_dropping_into_the_band_restarts_the_on_hold():
    detector, _ = settled(False)
    trace = feed(detector, 1.0, 1.16, 0.6)
    trace += feed(detector, 1.16, 1.18, 0.2)  # one sample between the thresholds
    trace += feed(detector, 1.18, 2.0, 0.6)
    assert first_time(trace, True) == pytest.approx(1.18 + CFG.on_hold_s)


def test_hold_time_is_measured_on_sample_times_not_on_update_calls():
    detector, _ = settled(False)
    # Ten updates, all carrying samples 1 ms apart: far less than on_hold_s of data, so no decision.
    for i in range(10):
        contact = detector.update(reading(1.0 + i * 0.001, 0.9), 1.0 + i * 0.001)
    assert contact.state is False


# --- contact: staleness ---

def test_stale_data_is_unknown_never_holding():
    detector, t_last = settled(True)
    assert detector.update(None, t_last + CFG.stale_s).state is True  # exactly at the limit: still fresh
    contact = detector.update(None, t_last + CFG.stale_s + 0.001)
    assert contact == Contact(state=None, level=None, since=pytest.approx(t_last + CFG.stale_s))
    assert detector.update(None, t_last + 60.0).since == pytest.approx(t_last + CFG.stale_s)


def test_stale_data_is_unknown_never_not_holding():
    detector, t_last = settled(False)
    assert detector.update(None, t_last + 1.0).state is None


def test_recovery_after_stale_data_needs_the_hold_time_again():
    detector, t_last = settled(True)
    assert detector.update(None, t_last + 1.0).state is None
    trace = feed(detector, t_last + 1.0, t_last + 2.0, 0.6)
    assert trace[0][1].state is None and trace[0][1].fresh
    assert first_time(trace, True) == pytest.approx(t_last + 1.0 + CFG.on_hold_s)


def test_after_stale_data_a_level_between_the_thresholds_stays_unknown():
    detector, t_last = settled(True)
    detector.update(None, t_last + 1.0)
    trace = feed(detector, t_last + 1.0, t_last + 3.0, 0.2)
    assert all(c.state is None and c.fresh for _, c in trace)  # what the band means depends on lost history


def test_a_gap_in_the_feed_resets_even_if_nobody_looked_during_it():
    detector, t_last = settled(True)
    contact = detector.update(reading(t_last + 1.0, 0.6), t_last + 1.0)  # first update since the gap
    assert contact.state is None and contact.since == pytest.approx(t_last + CFG.stale_s)


def test_a_reading_that_is_already_stale_when_it_arrives_is_unknown():
    detector = ContactDetector(CFG)
    contact = detector.update(reading(1.0, 0.6), 1.0 + CFG.stale_s + 0.01)  # it waited in a queue
    assert contact.state is None and not contact.fresh


def test_a_reading_stamped_in_the_future_cannot_keep_a_dead_feed_fresh():
    detector = ContactDetector(CFG)
    detector.update(reading(100.0, 0.6), 1.0)  # publisher clock far ahead
    assert detector.update(None, 1.0 + CFG.stale_s).fresh
    assert not detector.update(None, 1.0 + CFG.stale_s + 0.001).fresh


def test_the_same_reading_twice_does_not_extend_freshness():
    detector = ContactDetector(CFG)
    same = reading(1.0, 0.0)
    detector.update(same, 1.0)
    assert detector.update(same, 1.0 + CFG.stale_s).fresh
    assert not detector.update(same, 1.0 + CFG.stale_s + 0.001).fresh


def test_an_older_reading_is_ignored():
    detector = ContactDetector(CFG)
    detector.update(reading(1.0, 0.6), 1.0)
    assert detector.update(reading(0.99, 0.0), 1.01).level == pytest.approx(0.6)


# --- contact: every reading since the last call, however long a pass of the main loop took ---

def grip_then_open(t: float) -> float:
    """Open for 3 s, a firm grip for 4 s, then open again."""
    return 0.6 if 3.0 <= t < 7.0 else 0.0


SAMPLES = [reading(round(i * DT, 6), grip_then_open(round(i * DT, 6))) for i in range(601)]  # 0 to 12 s at 50 Hz


def after_each_sample() -> list[Contact]:
    """The reference: one update per sample, at its own sample time."""
    detector = ContactDetector(CFG)
    return [detector.update(r, r.t) for r in SAMPLES]


@pytest.mark.parametrize("period", [0.05, 0.24, 0.26, 0.30, 0.50, 2.0])
def test_the_decision_does_not_depend_on_how_often_the_loop_polls(period):
    # PressureReceiver.poll hands over everything since the last pass; a pass longer than stale_s used to see
    # only the newest reading, a hole of a whole pass, and so never decided at all.
    reference = after_each_sample()
    detector = ContactDetector(CFG)
    taken, polls, n = 0, [], 1
    while taken < len(SAMPLES):
        now = round(n * period, 6)
        batch = [r for r in SAMPLES[taken:] if r.t <= now]
        taken += len(batch)
        assert taken > 0
        contact = detector.update(batch or None, now)
        polls.append((now, contact))
        # Exactly what one update per sample said after the same newest sample (the times of decided states are
        # sample times, so they agree too).
        expected = reference[taken - 1]
        assert contact.state is expected.state and contact.level == expected.level and contact.fresh
        if contact.state is not None:
            assert contact.since == expected.since
        n += 1
    states = [c.state for _, c in polls]
    first = next(i for i, s in enumerate(states) if s is not None)
    assert None not in states[first:]  # once decided, never unknown again: the feed never stopped
    assert [s for i, s in enumerate(states[first:], first) if i == first or s != states[i - 1]] == [False, True, False]
    # Each change is seen within one period of the moment it happened on the samples.
    for state, at in [(False, CFG.off_hold_s), (True, 3.0 + CFG.on_hold_s), (False, 7.0 + CFG.off_hold_s)]:
        seen = next(now for now, c in polls if now >= at - 1e-9 and c.state is state)
        assert seen - at < period + 1e-9


def test_a_batch_whose_newest_reading_is_stale_is_unknown():
    detector, t_last = settled(True)
    batch = [reading(round(t_last + i * DT, 6), 0.6) for i in range(1, 11)]  # no hole, but it waited in a queue
    contact = detector.update(batch, batch[-1].t + CFG.stale_s + 0.01)
    assert contact == Contact(state=None, level=None, since=pytest.approx(batch[-1].t + CFG.stale_s))


def test_a_hole_inside_one_batch_resets_then_decides_again_after_the_hold_time():
    detector, t_last = settled(True)
    before = [reading(round(t_last + i * DT, 6), 0.6) for i in range(1, 6)]
    resumed = round(before[-1].t + 1.0, 6)  # the feed stopped for a second
    after = [reading(round(resumed + i * DT, 6), 0.6) for i in range(5)]  # 0.08 s of grip: less than on_hold_s
    contact = detector.update(before + after, after[-1].t + 0.005)
    assert contact.state is None and contact.fresh
    assert contact.since == pytest.approx(before[-1].t + CFG.stale_s)
    more = [reading(round(resumed + i * DT, 6), 0.6) for i in range(5, 15)]
    contact = detector.update(more, more[-1].t)
    assert contact.state is True and contact.since == pytest.approx(resumed + CFG.on_hold_s)


def test_a_grip_and_a_let_go_within_one_batch_end_not_holding():
    detector, t_last = settled(False)
    grip = [reading(round(t_last + i * DT, 6), 0.6) for i in range(1, 21)]           # 0.4 s: turns on
    let_go = [reading(round(grip[-1].t + i * DT, 6), 0.0) for i in range(1, 31)]     # 0.6 s: turns off again
    contact = detector.update(grip + let_go, let_go[-1].t)
    # `since` is the let-go's off time, so the state went True in between and False again.
    assert contact.state is False and contact.since == pytest.approx(let_go[0].t + CFG.off_hold_s)


def test_a_queue_overflow_shows_as_a_hole_and_resets():
    # After a long stall the socket keeps the oldest messages and drops the newer ones until it is read again.
    detector, t_last = settled(True)
    kept = [reading(round(t_last + i * DT, 6), 0.6) for i in range(1, 1001)]       # 20 s, still gripping
    newest = [reading(round(kept[-1].t + 10.0 + i * DT, 6), 0.6) for i in range(3)]  # 10 s of messages lost
    contact = detector.update(kept + newest, newest[-1].t)
    assert contact.state is None and contact.fresh
    assert contact.since == pytest.approx(kept[-1].t + CFG.stale_s)


def test_repeated_or_out_of_order_readings_within_a_batch_are_ignored():
    detector, t_last = settled(False)
    t1 = round(t_last + DT, 6)
    contact = detector.update([reading(t1, 0.0), reading(t1, 0.9), reading(t1 - 0.01, 0.9)], t1)
    assert contact.state is False and contact.level == pytest.approx(0.0)


def test_reset_forgets_everything():
    detector, t_last = settled(True)
    detector.reset()
    assert detector.update(None, t_last) == Contact(None, None, t_last)


def test_the_detector_refuses_an_unknown_combine_rule():
    with pytest.raises(ValueError, match="combine"):
        ContactDetector(dataclasses.replace(CFG, combine="max"))


# --- combining the pads ---

LT, LB, RT, RB = range(4)


def pads(**levels) -> tuple:
    values = [0.0] * 4
    for name, v in levels.items():
        values[{"lt": LT, "lb": LB, "rt": RT, "rb": RB}[name]] = v
    return tuple(values)


@pytest.mark.parametrize("norm, both, any_pad, mean", [
    (pads(lt=0.8, rt=0.6), 0.6, 0.8, 0.35),                   # fingertip pinch
    (pads(lb=0.5, rb=0.7), 0.5, 0.7, 0.3),                    # deep in the jaws
    (pads(lt=0.6, rb=0.5), 0.5, 0.6, 0.275),                  # tilted object: tip on one side, base on the other
    (pads(lt=0.9), 0.0, 0.9, 0.225),                          # one finger only: a bump or a push
    (pads(lt=0.9, lb=0.9), 0.0, 0.9, 0.45),                   # one whole finger pressed on the table
    (pads(lt=0.4, lb=0.2, rt=0.1, rb=0.3), 0.3, 0.4, 0.25),
])
def test_grip_level_rules(norm, both, any_pad, mean):
    assert grip_level(norm, "both_fingers") == pytest.approx(both)
    assert grip_level(norm, "any_pad") == pytest.approx(any_pad)
    assert grip_level(norm, "mean") == pytest.approx(mean)


def test_one_finger_pressed_is_not_a_grasp_by_default_but_is_with_any_pad():
    one_finger = pads(lt=0.9, lb=0.9)
    results = {}
    for rule in COMBINE_RULES:
        detector = ContactDetector(dataclasses.replace(CFG, combine=rule))
        trace = [(i * DT, detector.update(reading(i * DT, one_finger), i * DT)) for i in range(100)]
        results[rule] = trace[-1][1].state
    assert results == {"both_fingers": False, "any_pad": True, "mean": True}


def test_grip_level_needs_four_pads_and_a_known_rule():
    with pytest.raises(ValueError):
        grip_level((0.5, 0.5, 0.5), "both_fingers")
    with pytest.raises(ValueError):
        grip_level((0.5,) * 4, "median")


# --- calibration ---

def test_normalize_scales_and_clips_each_pad():
    cal = PressureCalibrationConfig(raw_zero=[100, 200, 0, 0], raw_full=[1100, 1200, 4095, 4095])
    assert normalize((600, 0, 5000, 4095), cal) == pytest.approx((0.5, 0.0, 1.0, 1.0))


def test_normalize_handles_a_circuit_that_reads_lower_under_pressure():
    cal = PressureCalibrationConfig(raw_zero=[3000] * 4, raw_full=[1000] * 4)
    assert normalize((3000, 2000, 1000, 3500), cal) == pytest.approx((0.0, 0.5, 1.0, 0.0))


def test_denormalize_inverts_normalize():
    norm = (0.0, 0.25, 0.5, 1.0)
    assert normalize(denormalize(norm, CAL), CAL) == pytest.approx(norm)


# --- the message ---

CONSOLE_EXAMPLE = '{"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}'


def test_the_console_example_is_valid():
    _, msg = decode(f"{TOPIC_PRESSURE} {CONSOLE_EXAMPLE}".encode())
    validate_pressure(msg)
    assert PressureReading.from_message(msg) == PressureReading(
        t=1731542400.131, raw=(1840.0, 1322.0, 1795.0, 1290.0), norm=(0.52, 0.36, 0.5, 0.35))


def test_build_pressure_message_is_compact_and_valid():
    msg = build_pressure_message(t=1731542400.13149, raw=(1840.0, 1322.0, 1795.5, 1290.0),
                                 norm=(0.52049, 0.36, 0.5, 0.35))
    assert msg == {"t": 1731542400.1315, "raw": [1840, 1322, 1795.5, 1290], "norm": [0.52, 0.36, 0.5, 0.35]}
    assert encode(TOPIC_PRESSURE, msg).startswith(b'pressure {"t":1731542400.1315,"raw":[1840,')
    assert isinstance(msg["raw"][0], int)


@pytest.mark.parametrize("norm", [(0.5, 0.5, 0.5, 1.2), (0.5, 0.5, 0.5, -0.1), (0.5,) * 3, (0.5, 0.5, 0.5, math.nan)])
def test_build_pressure_message_refuses_what_it_could_not_send(norm):
    with pytest.raises(ValueError, match="invalid pressure message"):
        build_pressure_message(t=1.0, raw=(1, 2, 3, 4)[:len(norm)], norm=norm)


def _valid() -> dict:
    return {"t": 1731542400.131, "raw": [1840, 1322, 1795, 1290], "norm": [0.52, 0.36, 0.5, 0.35]}


def _with(**changes) -> dict:
    msg = _valid()
    msg.update(changes)
    return msg


def _without(key: str) -> dict:
    msg = _valid()
    del msg[key]
    return msg


@pytest.mark.parametrize("msg, problem", [
    # Everything the web console's validatePressure rejects:
    ([], "not an object"),
    (None, "not an object"),
    ("pressure", "not an object"),
    (_without("t"), "keys"),
    (_without("raw"), "keys"),
    (_without("norm"), "keys"),
    (_with(t="1731542400"), "t is not"),
    (_with(t=None), "t is not"),
    (_with(t=True), "t is not"),          # a JSON boolean is not a number
    (_with(t=math.nan), "t is not"),
    (_with(t=math.inf), "t is not"),
    (_with(raw=[1, 2, 3]), "raw"),
    (_with(raw=[1, 2, 3, 4, 5]), "raw"),
    (_with(raw=[1, 2, "3", 4]), "raw"),
    (_with(raw=[1, 2, None, 4]), "raw"),
    (_with(raw=[1, 2, True, 4]), "raw"),
    (_with(raw=[1, 2, math.inf, 4]), "raw"),
    (_with(raw={"0": 1}), "raw"),
    (_with(raw=1840), "raw"),
    (_with(norm=[0.1, 0.2, 0.3]), "norm"),
    (_with(norm=[0.1, 0.2, 0.3, 1.01]), "norm"),
    (_with(norm=[0.1, 0.2, 0.3, -0.01]), "norm"),
    (_with(norm=[0.1, 0.2, 0.3, math.nan]), "norm"),
    (_with(norm=[0.1, 0.2, 0.3, False]), "norm"),
    (_with(norm=[0.1, 0.2, 0.3, "0.4"]), "norm"),
    (_with(norm=None), "norm"),
    # Stricter than the console, like validate_vision: no extra keys.
    (_with(seq=12), "keys"),
])
def test_validate_pressure_rejects(msg, problem):
    with pytest.raises(ValueError, match=f"invalid pressure message: .*{problem}"):
        validate_pressure(msg)


def test_validate_pressure_accepts_the_edges():
    validate_pressure(_with(t=0, raw=[-5, 0, 1e6, 3.5], norm=[0, 1, 0.0, 1.0]))


def test_the_pad_count_agrees_between_config_and_messages():
    assert config_module.PRESSURE_PADS == PRESSURE_PADS == 4


# --- the receiver and the publisher, over real sockets ---

def connect(publisher: PressurePublisher, receiver: PressureReceiver) -> None:
    """PUB/SUB drops messages until the subscription has reached the publisher."""
    deadline = time.monotonic() + 5
    while receiver.poll() is None:
        assert time.monotonic() < deadline, "the receiver never connected"
        publisher.publish(build_pressure_message(t=1.0, raw=(0,) * 4, norm=(0,) * 4))
        time.sleep(0.02)
    time.sleep(0.1)
    receiver.poll()  # drain


def poll_until(receiver: PressureReceiver, seconds: float) -> list[PressureReading]:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        r = receiver.poll()
        if r is not None:
            out.extend(r)
        time.sleep(0.01)
    return out


def test_poll_returns_every_reading_since_the_last_call_in_order():
    # Not only the newest: the detector needs them all, or a slow pass looks like a hole in the feed.
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        connect(pub, receiver)
        received_before = receiver.received
        for i in range(5):
            pub.publish(build_pressure_message(t=10.0 + i, raw=(i,) * 4, norm=(i / 10,) * 4))
        time.sleep(0.2)
        assert receiver.poll() == [PressureReading(t=10.0 + i, raw=(float(i),) * 4, norm=(i / 10,) * 4)
                                   for i in range(5)]
        assert receiver.received - received_before == 5
        assert receiver.poll() is None  # nothing new


def test_the_receiver_ignores_invalid_messages_and_other_topics(caplog):
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        connect(pub, receiver)
        caplog.set_level(logging.WARNING)
        good = build_pressure_message(t=20.0, raw=(1, 2, 3, 4), norm=(0.1, 0.2, 0.3, 0.4))
        sock = pub._sock  # send what a broken publisher would
        sock.send(encode(TOPIC_PRESSURE, good))
        sock.send(b"pressure {not json")
        sock.send(b"pressure \xff\xfe")
        sock.send(encode(TOPIC_PRESSURE, {**good, "norm": [0.1, 0.2, 0.3, 1.5]}))
        sock.send(encode("vision", good))
        sock.send(encode("pressures", good))
        time.sleep(0.2)
        assert receiver.poll() == [PressureReading.from_message(good)]  # only the valid one
    assert receiver.rejected == 3
    assert caplog.text.count("pressure: ignored") == 1  # the first one; the rest wait for the summary


def test_a_flood_of_invalid_messages_cannot_flood_the_log(caplog):
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        connect(pub, receiver)
        caplog.set_level(logging.WARNING)
        for _ in range(500):
            pub._sock.send(b"pressure {}")
        time.sleep(0.3)
        assert receiver.poll() is None
        assert caplog.text.count("pressure: ignored") == 1
        receiver._WARN_EVERY_S = 0.0  # the summary is due
        pub._sock.send(b"pressure {}")
        time.sleep(0.1)
        receiver.poll()
    assert receiver.rejected == 501
    assert "ignored 500 more invalid messages" in caplog.text


def test_poll_returns_at_once_when_no_one_publishes():
    with socket.socket() as probe:  # a port that nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with PressureReceiver(f"tcp://127.0.0.1:{port}") as receiver:
        started = time.monotonic()
        for _ in range(1000):
            assert receiver.poll() is None
        assert time.monotonic() - started < 0.5


def test_a_malformed_endpoint_is_reported_clearly():
    with pytest.raises(RuntimeError, match="pressure subscriber"):
        PressureReceiver("not an endpoint")


def test_a_second_publisher_on_the_same_port_is_refused_clearly():
    with PressurePublisher(ANY_PORT) as pub:
        with pytest.raises(RuntimeError, match="Is another mock or bridge running"):
            PressurePublisher(pub.endpoint)


def test_the_publisher_log_holds_the_wire_text(tmp_path):
    path = tmp_path / "pressure.log"
    msgs = [build_pressure_message(t=1.0 + i * DT, raw=(i,) * 4, norm=(0.1,) * 4) for i in range(3)]
    with PressurePublisher(ANY_PORT, log_path=path) as pub:
        for msg in msgs:
            pub.publish(msg)
        assert pub.sent == 3
    assert path.read_text(encoding="utf-8").splitlines() == [encode(TOPIC_PRESSURE, m).decode() for m in msgs]


def test_detector_over_zmq_follows_the_published_level():
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        connect(pub, receiver)
        detector = ContactDetector(CFG)
        states = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.4:
            elapsed = time.monotonic() - t0
            level = 0.6 if 0.1 < elapsed < 0.6 else 0.0  # on after 0.3 s, off after 1.0 s
            pub.publish(build_pressure_message(t=clock.now(), raw=(0,) * 4, norm=(level,) * 4))
            time.sleep(DT)
            contact = detector.update(receiver.poll(), clock.now())
            if not states or states[-1] != contact.state:
                states.append(contact.state)
    assert states == [None, True, False]


def test_a_reader_slower_than_stale_s_over_zmq_still_decides():
    # The main loop may take longer than stale_s for one pass (a slow detector); the feed itself never stopped.
    with PressurePublisher(ANY_PORT) as pub, PressureReceiver(pub.endpoint) as receiver:
        connect(pub, receiver)
        detector = ContactDetector(CFG)
        contacts = []
        t0 = time.monotonic()
        next_poll = t0 + 0.35
        while time.monotonic() - t0 < 1.5:
            pub.publish(build_pressure_message(t=clock.now(), raw=(0,) * 4, norm=(0.6,) * 4))
            time.sleep(DT)
            if time.monotonic() >= next_poll:  # one pass every 0.35 s, longer than stale_s
                contacts.append(detector.update(receiver.poll(), clock.now()))
                next_poll += 0.35
    assert len(contacts) >= 3
    assert contacts[-1].state is True and contacts[-1].fresh


# --- the config section ---

@pytest.fixture
def raw_yaml():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_the_shipped_pressure_section_loads_and_is_consistent(shipped_cfg):
    p = shipped_cfg.pressure
    validate_pressure_config(p)
    assert p.combine in COMBINE_RULES
    assert p.off_threshold < p.on_threshold <= p.mock.contact_level


def test_the_test_config_is_valid():
    validate_pressure_config(CFG)


@pytest.mark.parametrize("changes, message", [
    (dict(combine="max"), "pressure.combine must be one of"),
    (dict(off_threshold=0.3), "off_threshold < pressure.on_threshold"),   # equal: no hysteresis
    (dict(off_threshold=0.4), "off_threshold < pressure.on_threshold"),
    (dict(off_threshold=-0.1), "off_threshold < pressure.on_threshold"),
    (dict(on_threshold=1.2), "off_threshold < pressure.on_threshold"),
    (dict(on_hold_s=-0.1), "on_hold_s and pressure.off_hold_s must not be negative"),
    (dict(off_hold_s=-0.1), "on_hold_s and pressure.off_hold_s must not be negative"),
    (dict(stale_s=0.0), "stale_s must be above 0"),
    (dict(calibration=PressureCalibrationConfig(raw_zero=[0, 0, 0], raw_full=[1, 1, 1, 1])), "4 values each"),
    (dict(calibration=PressureCalibrationConfig(raw_zero=[0] * 4, raw_full=[1] * 5)), "4 values each"),
    (dict(calibration=PressureCalibrationConfig(raw_zero=[0] * 4, raw_full=[4095, 0, 4095, 4095])), "must differ"),
    (dict(calibration=PressureCalibrationConfig(raw_zero=[0] * 4, raw_full=[math.inf] * 4)), "finite"),
    (dict(calibration=PressureCalibrationConfig(raw_zero=[math.nan] * 4, raw_full=[1] * 4)), "finite"),
    (dict(mock=dataclasses.replace(MOCK, rate_hz=0)), "rate_hz must be above 0"),
    (dict(mock=dataclasses.replace(MOCK, rate_hz=4)), "above 1 / pressure.stale_s"),  # 0.25 s between samples
    (dict(mock=dataclasses.replace(MOCK, ramp_s=-0.1)), "ramp_s must not be negative"),
    (dict(mock=dataclasses.replace(MOCK, contact_level=0.2)), "contact_level must be in"),  # below on_threshold
    (dict(mock=dataclasses.replace(MOCK, contact_level=1.1)), "contact_level must be in"),
    (dict(bridge=dataclasses.replace(BRIDGE, baud=0)), "baud must be above 0"),
    (dict(bridge=dataclasses.replace(BRIDGE, read_timeout_s=0)), "read_timeout_s, reopen_s and report_s"),
    (dict(bridge=dataclasses.replace(BRIDGE, reopen_s=0)), "read_timeout_s, reopen_s and report_s"),
    (dict(bridge=dataclasses.replace(BRIDGE, report_s=-1)), "read_timeout_s, reopen_s and report_s"),
])
def test_pressure_config_range_checks(changes, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        validate_pressure_config(dataclasses.replace(CFG, **changes))


def test_validate_checks_the_pressure_section(raw_yaml):
    data = copy.deepcopy(raw_yaml)
    data["pressure"]["off_threshold"] = data["pressure"]["on_threshold"]
    with pytest.raises(ConfigError, match="off_threshold"):
        from_dict(data)


@pytest.mark.parametrize("edit, message", [
    (lambda p: p.pop("stale_s"), r"config\.pressure: missing key\(s\) \['stale_s'\]"),
    (lambda p: p.update(threshold=0.3), r"config\.pressure: unknown key\(s\) \['threshold'\]"),
    (lambda p: p["calibration"].pop("raw_full"), r"config\.pressure\.calibration: missing"),
    (lambda p: p["bridge"].update(baud=115200.5), r"config\.pressure\.bridge\.baud: expected int"),
    (lambda p: p["calibration"].update(raw_zero=0), r"config\.pressure\.calibration\.raw_zero: expected a list"),
])
def test_the_pressure_section_is_loaded_strictly(raw_yaml, edit, message):
    data = copy.deepcopy(raw_yaml)
    edit(data["pressure"])
    with pytest.raises(ConfigError, match=message):
        from_dict(data)


def test_a_config_without_a_pressure_section_is_refused(raw_yaml):
    data = copy.deepcopy(raw_yaml)
    del data["pressure"]
    with pytest.raises(ConfigError, match=r"missing key\(s\) \['pressure'\]"):
        from_dict(data)
