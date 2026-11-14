import copy
import json

import pytest
from conftest import track

from paul_vision.messages import TOPIC_VISION, build_vision_message, decode, encode, validate_vision
from paul_vision.phase import PHASES, PhaseMachine
from paul_vision.types import STATUSES, Guidance

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
    }
    validate_vision(ok_message())


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


def test_phase_machine_m1():
    machine = PhaseMachine()
    assert machine.phase == "search"
    assert machine.update(has_target=True) == "approach"
    assert machine.update(has_target=False) == "search"
    assert {"search", "approach", "grasp", "carry", "place", "release"} == set(PHASES)
