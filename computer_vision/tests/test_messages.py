import copy
import json

import pytest
from conftest import track

from paul_vision.messages import (TOPIC_VISION, build_vision_message, decode, encode, is_number, validate_pressure,
                                  validate_vision, wire_bbox)
from paul_vision.phase import PHASES
from paul_vision.types import STATUSES, Guidance, Track

GUIDANCE = Guidance(dx=-0.1234567, dy=0.05, distance_hint="near", aligned=False)


def ok_message(**overrides):
    fields = dict(t=1731542400.123456, frame_id=1042, status="ok", phase="approach",
                  target=track(7, "cup", 0.912345, cx=0.515, cy=0.44, w=0.21, h=0.28), guidance=GUIDANCE,
                  summary="Cup near, slightly left.")
    fields.update(overrides)
    return build_vision_message(**fields)


def test_ok_message_matches_the_contract():
    assert ok_message() == {
        "t": 1731542400.1235,
        "frame_id": 1042,
        "status": "ok",
        "phase": "approach",
        "target": {"track_id": 7, "label": "cup", "source": "yolo", "bbox": [0.41, 0.3, 0.62, 0.58],
                   "confidence": 0.912},
        "guidance": {"dx": -0.123, "dy": 0.05, "distance_hint": "near", "aligned": False},
        "summary": "Cup near, slightly left.",
        "alert": None,
    }
    validate_vision(ok_message())


def test_keys_are_exactly_the_contract():
    assert list(ok_message()) == ["t", "frame_id", "status", "phase", "target", "guidance", "summary", "alert"]


@pytest.mark.parametrize("status", STATUSES)
def test_the_dropped_alert_goes_with_any_status(status):
    # A drop matters whether or not a target is in view, and during a stall too.
    msg = ok_message(status=status, phase="search", alert="dropped", summary=None)
    assert msg["alert"] == "dropped"
    validate_vision(msg)


@pytest.mark.parametrize("alert", ["Dropped", "drop", "", "fire"])
def test_an_unknown_alert_is_refused_when_built(alert):
    with pytest.raises(ValueError, match="unknown alert"):
        ok_message(alert=alert)


@pytest.mark.parametrize("phase", PHASES)
def test_every_phase_is_valid_on_the_wire(phase):
    validate_vision(ok_message(phase=phase))


def test_a_station_target_is_published_with_its_source():
    station = Track(3, "drop station", 0.97, (0.3, 0.3, 0.5, 0.5), "aruco")
    msg = ok_message(phase="carry", target=station)
    assert msg["target"]["source"] == "aruco" and msg["target"]["label"] == "drop station"
    validate_vision(msg)


# --- boxes on the wire: never degenerate ---

@pytest.mark.parametrize("bbox", [
    (0.12341, 0.5, 0.12344, 0.6),          # a sliver that 4-decimal rounding would collapse
    (0.5, 0.20001, 0.6, 0.20004),
    (0.99996, 0.1, 0.99999, 0.2),          # at the right edge: must not step past 1
    (0.0, 0.0, 0.00001, 0.00002),          # at the corner
    (1.0, 1.0, 1.0, 1.0),                  # a point on the far corner
    (0.3, 0.3, 0.3, 0.3),                  # a point
    (-0.2, 0.4, 0.00001, 0.5),             # outside the frame on one side
])
def test_a_box_that_rounding_would_collapse_stays_a_box(bbox):
    wire = wire_bbox(bbox)
    assert wire[0] < wire[2] and wire[1] < wire[3]
    assert all(0 <= v <= 1 for v in wire)
    assert all(v == round(v, 4) for v in wire)
    msg = ok_message(target=Track(1, "cup", 0.9, bbox))
    validate_vision(msg)
    validate_vision(decode(encode(TOPIC_VISION, msg))[1])  # and after the JSON round trip


def test_a_collapsed_span_is_one_step_wide_at_the_rounded_lower_edge():
    assert wire_bbox((0.12341, 0.5, 0.12344, 0.6)) == [0.1234, 0.5, 0.1235, 0.6]
    assert wire_bbox((0.99996, 0.1, 0.99999, 0.2)) == [0.9999, 0.1, 1.0, 0.2]


def test_a_box_that_survives_rounding_is_sent_as_rounded():
    assert wire_bbox((0.123456, 0.2, 0.654321, 0.8)) == [0.1235, 0.2, 0.6543, 0.8]


@pytest.mark.parametrize("bbox", [[0.3, 0.2, 0.3, 0.4], [0.1, 0.4, 0.3, 0.4], [0.3, 0.2, 0.1, 0.4]])
def test_validate_rejects_a_degenerate_box(bbox):
    msg = copy.deepcopy(ok_message())
    msg["target"]["bbox"] = bbox
    with pytest.raises(ValueError, match="x1 < x2"):
        validate_vision(msg)


@pytest.mark.parametrize("status", [s for s in STATUSES if s != "ok"])
def test_target_and_guidance_are_null_whenever_status_is_not_ok(status):
    # Even if a caller passes a target by mistake, it must not reach the wire.
    msg = ok_message(status=status, phase="search", summary=None)
    assert msg["target"] is None and msg["guidance"] is None
    validate_vision(msg)


def test_ok_without_target_or_guidance_is_refused():
    with pytest.raises(ValueError):
        ok_message(target=None)
    with pytest.raises(ValueError):
        ok_message(guidance=None)


def test_wire_format_is_one_frame_with_a_topic_prefix():
    data = encode(TOPIC_VISION, ok_message())
    assert isinstance(data, bytes)
    assert data.startswith(b'vision {"t":')
    assert json.loads(data[len(b"vision "):]) == ok_message()
    assert b"\n" not in data and b": " not in data  # compact JSON


def test_encode_decode_round_trip():
    msg = ok_message(summary="Pill bottle ahead, left and slightly up.")
    assert decode(encode(TOPIC_VISION, msg)) == (TOPIC_VISION, msg)


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("summary"),
    lambda m: m.pop("alert"),
    lambda m: m.update(alert="Dropped"),
    lambda m: m.update(alert=True),
    lambda m: m.update(extra=1),
    lambda m: m.update(status="fine"),
    lambda m: m.update(phase="lunch"),
    lambda m: m.update(t="now"),
    lambda m: m.update(frame_id=1.5),
    lambda m: m.update(summary=""),
    lambda m: m.update(target=None),
    lambda m: m.update(guidance=None),
    lambda m: m.update(status="no_target"),              # target/guidance still present
    lambda m: m["target"].update(bbox=[0.1, 0.2, 0.3]),
    lambda m: m["target"].update(bbox=[0.6, 0.2, 0.3, 0.4]),
    lambda m: m["target"].update(bbox=[0.1, 0.2, 0.3, 1.4]),
    lambda m: m["target"].update(confidence=1.2),
    lambda m: m["target"].pop("source"),
    lambda m: m["guidance"].update(dx=1.5),
    lambda m: m["guidance"].update(dy=float("nan")),
    lambda m: m["guidance"].update(distance_hint="close"),
    lambda m: m["guidance"].update(aligned="yes"),
])
def test_validate_rejects_broken_messages(mutate):
    msg = copy.deepcopy(ok_message())
    mutate(msg)
    with pytest.raises(ValueError):
        validate_vision(msg)


def test_the_contract_names_six_phases():
    assert PHASES == ("search", "approach", "grasp", "carry", "place", "release")


# --- what a socket may bring: nothing in it may raise anything but ValueError ---

HUGE = int("9" * 400)  # JSON allows an int this large; float() and math.isfinite() overflow on it


@pytest.mark.parametrize("body", [b"[" * 5000 + b"]" * 5000, b"[" * 5000, b'{"a":' * 5000 + b"1" + b"}" * 5000])
def test_decode_refuses_json_nested_too_deeply_with_a_value_error(body):
    with pytest.raises(ValueError, match="nested too deeply"):
        decode(b"command " + body)


@pytest.mark.parametrize("data", [b"command {not json", b"command \xff\xfe", b"\xff {}", b"command "])
def test_decode_refuses_what_cannot_be_read_with_a_value_error(data):
    with pytest.raises(ValueError):
        decode(data)


def test_decode_reads_a_huge_int_as_an_int():
    assert decode(b'command {"x":%s}' % str(HUGE).encode()) == ("command", {"x": HUGE})


@pytest.mark.parametrize("value, expected", [
    (0, True), (-3, True), (0.5, True), (10 ** 300, True), (HUGE, False), (-HUGE, False),
    (float("nan"), False), (float("inf"), False), (True, False), (False, False), ("1", False), (None, False),
])
def test_is_number(value, expected):
    assert is_number(value) is expected


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(t=HUGE),
    lambda m: m["target"].update(bbox=[0.1, 0.2, HUGE, 0.4]),
    lambda m: m["target"].update(confidence=HUGE),
    lambda m: m["guidance"].update(dx=-HUGE),
])
def test_validate_vision_refuses_a_huge_int_with_a_value_error(mutate):
    msg = copy.deepcopy(ok_message())
    mutate(msg)
    with pytest.raises(ValueError):
        validate_vision(msg)


@pytest.mark.parametrize("msg", [
    {"t": HUGE, "raw": [1, 2, 3, 4], "norm": [0.1, 0.2, 0.3, 0.4]},
    {"t": 1.0, "raw": [1, 2, HUGE, 4], "norm": [0.1, 0.2, 0.3, 0.4]},
    {"t": 1.0, "raw": [1, 2, 3, 4], "norm": [0.1, 0.2, 0.3, HUGE]},
])
def test_validate_pressure_refuses_a_huge_int_with_a_value_error(msg):
    validate_pressure({"t": 1.0, "raw": [1, 2, 3, 4], "norm": [0.1, 0.2, 0.3, 0.4]})  # the same, without it
    with pytest.raises(ValueError):
        validate_pressure(msg)
