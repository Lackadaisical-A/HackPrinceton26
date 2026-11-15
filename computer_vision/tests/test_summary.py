import pytest

from paul_vision.config import SummaryConfig
from paul_vision.summary import (EVENT_TEXT, TEXT_FIND_DROP, TEXT_NO_TARGET, TEXT_TARGET_LOST, Summarizer,
                                direction_phrase)
from paul_vision.types import Guidance

CFG = SummaryConfig(min_interval_s=2.0, settle_s=0.4, center_band=0.10, slight_band=0.35, band_hysteresis=0.15)


def guide(dx=0.0, dy=0.0, hint="far", aligned=False):
    return Guidance(dx, dy, hint, aligned)


def run(summarizer, t_from, t_to, status="ok", label="cup", guidance=None, step=0.05, **kwargs):
    """Feed the same input repeatedly; returns [(t, text)] for every utterance."""
    spoken, t = [], t_from
    while t <= t_to + 1e-9:
        text = summarizer.update(status, label if status == "ok" else None,
                                 (guidance or guide()) if status == "ok" else None, round(t, 6), **kwargs)
        if text:
            spoken.append((round(t, 2), text))
        t += step
    return spoken


# --- wording ---

@pytest.mark.parametrize("aligned, horizontal, vertical, phrase", [
    (True, (1, 1), (2, -1), "centered"),
    (False, (0, 1), (0, 1), "nearly centered"),
    (False, (1, -1), (0, 1), "slightly left"),
    (False, (2, 1), (0, 1), "right"),
    (False, (0, 1), (1, 1), "slightly down"),
    (False, (0, 1), (2, -1), "up"),
    (False, (1, -1), (1, -1), "slightly left and up"),
    (False, (2, 1), (2, 1), "right and down"),
    (False, (2, -1), (1, 1), "left and slightly down"),
    (False, (1, 1), (2, -1), "up and slightly right"),
])
def test_direction_phrase(aligned, horizontal, vertical, phrase):
    assert direction_phrase(aligned, horizontal, vertical) == phrase


@pytest.mark.parametrize("label, guidance, text", [
    ("cup", guide(dx=-0.2), "Cup ahead, slightly left."),
    ("cup", guide(dx=0.6, dy=-0.5, hint="near"), "Cup near, right and up."),
    ("pill bottle", guide(hint="in_reach", aligned=True), "Pill bottle in reach, centered."),
])
def test_utterance_text(label, guidance, text):
    assert run(Summarizer(CFG), 0.0, 1.0, label=label, guidance=guidance) == [(0.4, text)]


# --- timing ---

def test_nothing_is_said_before_the_text_has_settled():
    s = Summarizer(CFG)
    assert run(s, 0.0, 0.35, guidance=guide(dx=-0.2)) == []
    assert run(s, 0.40, 0.40, guidance=guide(dx=-0.2)) == [(0.4, "Cup ahead, slightly left.")]


def test_the_same_text_is_never_repeated():
    assert len(run(Summarizer(CFG), 0.0, 20.0, guidance=guide(dx=-0.2))) == 1


def test_changes_are_rate_limited():
    s = Summarizer(CFG)
    first = run(s, 0.0, 1.0, guidance=guide(dx=-0.2))
    second = run(s, 1.05, 4.0, guidance=guide(dx=0.6))
    assert first == [(0.4, "Cup ahead, slightly left.")]
    assert second == [(2.4, "Cup ahead, right.")]  # settled at 1.45, but held until 2 s after the first


def test_text_that_keeps_changing_is_never_spoken():
    s = Summarizer(CFG)
    spoken = []
    for i in range(100):  # the wording alternates every 0.2 s, faster than settle_s
        g = guide(dx=-0.6) if (i // 4) % 2 else guide(dx=0.6)
        spoken.append(s.update("ok", "cup", g, i * 0.05))
    assert not any(spoken)


def test_a_brief_change_that_reverts_is_not_spoken():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, guidance=guide(dx=-0.2))
    assert run(s, 3.05, 3.25, guidance=guide(dx=0.6)) == []   # 0.2 s blip
    assert run(s, 3.30, 8.0, guidance=guide(dx=-0.2)) == []   # back to what was already said


# --- losing the target ---

def test_no_target_is_announced_once_at_startup():
    assert run(Summarizer(CFG), 0.0, 10.0, status="no_target") == [(0.4, TEXT_NO_TARGET)]


def test_losing_and_finding_the_target():
    s = Summarizer(CFG)
    assert run(s, 0.0, 3.0, guidance=guide(dx=-0.2)) == [(0.4, "Cup ahead, slightly left.")]
    assert run(s, 3.05, 6.0, status="no_target") == [(3.45, TEXT_TARGET_LOST)]
    assert run(s, 6.05, 9.0, status="low_confidence") == []  # still "Target lost."
    found = run(s, 9.05, 12.0, guidance=guide(dx=-0.2))
    assert [text for _, text in found] == ["Cup ahead, slightly left."]
    assert 9.45 <= found[0][0] <= 9.5  # settle_s after reappearing (9.45 - 9.05 is just under 0.4 in floats)


def test_a_dropout_shorter_than_settle_is_not_announced():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, guidance=guide(dx=-0.2))
    assert run(s, 3.05, 3.30, status="no_target") == []
    assert run(s, 3.35, 6.0, guidance=guide(dx=-0.2)) == []


# --- wording hysteresis ---

def test_wording_does_not_flicker_on_a_band_boundary():
    s = Summarizer(CFG)
    spoken = []
    for i in range(400):  # dx wobbles +-10% around slight_band, changing every 0.5 s
        dx = 0.35 * (1.1 if (i // 10) % 2 else 0.9)
        spoken.append(s.update("ok", "cup", guide(dx=dx), i * 0.05))
    assert [text for text in spoken if text] == ["Cup ahead, slightly right."]


def test_target_change_resets_wording_hysteresis():
    s = Summarizer(CFG)
    run(s, 0.0, 1.0, guidance=guide(dx=0.6))                       # "right"
    assert run(s, 1.05, 4.0, guidance=guide(dx=0.32)) == []        # still "right" thanks to hysteresis
    said = run(s, 4.05, 6.0, guidance=guide(dx=0.32), label="box", target_changed=True)
    assert said == [(4.45, "Box ahead, slightly right.")]


def test_crossing_the_centre_flips_the_side():
    s = Summarizer(CFG)
    run(s, 0.0, 1.0, guidance=guide(dx=0.2))
    assert run(s, 1.05, 4.0, guidance=guide(dx=-0.2)) == [(2.4, "Cup ahead, slightly left.")]


def test_reset_makes_the_current_state_be_announced_again():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, guidance=guide(dx=-0.2))
    s.reset()
    assert run(s, 3.05, 5.0, guidance=guide(dx=-0.2)) == [(3.45, "Cup ahead, slightly left.")]


# --- phases and events (M2) ---

@pytest.mark.parametrize("event, text", list(EVENT_TEXT.items()))
def test_an_event_is_spoken_on_the_frame_it_happens(event, text):
    s = Summarizer(CFG)
    run(s, 0.0, 1.0, guidance=guide(dx=-0.2))                       # a sentence was said at 0.4
    assert s.update("ok", "cup", guide(dx=-0.2), 1.05, phase="grasp", events=(event,)) == text  # inside min_interval
    assert s.update("ok", "cup", guide(dx=-0.2), 1.10, phase="grasp") is None                  # and only once


def test_events_on_the_same_frame_are_spoken_together():
    s = Summarizer(CFG)
    said = s.update("no_target", None, None, 0.0, phase="search", events=("sensor_lost", "dropped"))
    assert said == "Grip sensor lost. Grip lost. Object may have dropped."


def test_unknown_events_are_not_spoken():
    s = Summarizer(CFG)
    assert s.update("no_target", None, None, 0.0, phase="search", events=("bump",)) is None


def test_an_event_counts_as_the_last_utterance_for_the_rate_limit():
    s = Summarizer(CFG)
    assert s.update("no_target", None, None, 0.0, phase="carry", events=("grip",)) == "Grip established."
    said = run(s, 0.05, 4.0, status="no_target", phase="carry")
    assert said == [(2.0, TEXT_FIND_DROP)]  # settled at 0.4, held until min_interval after the event


def test_the_drop_sentence_claims_no_more_than_the_pads_know():
    # The pads know contact ended away from the drop station; not that the object fell.
    text = EVENT_TEXT["dropped"]
    assert "may" in text and "dropped" in text


def test_carry_without_the_drop_station_in_view_says_so():
    assert run(Summarizer(CFG), 0.0, 3.0, status="no_target", phase="carry") == [(0.4, TEXT_FIND_DROP)]


def test_carry_speaks_about_the_drop_station_when_it_is_the_target():
    said = run(Summarizer(CFG), 0.0, 3.0, label="drop station", guidance=guide(dx=0.6, hint="near"), phase="carry")
    assert said == [(0.4, "Drop station near, right.")]


@pytest.mark.parametrize("phase", ["grasp", "place", "release"])
def test_busy_phases_say_nothing_without_a_target(phase):
    # The gripper is at the object or at the drop station; "Target lost." would be noise.
    assert run(Summarizer(CFG), 0.0, 5.0, status="no_target", phase=phase) == []


@pytest.mark.parametrize("phase", [None, "search", "approach"])
def test_object_phases_speak_as_milestone_1(phase):
    s = Summarizer(CFG)
    assert run(s, 0.0, 3.0, guidance=guide(dx=-0.2), phase=phase) == [(0.4, "Cup ahead, slightly left.")]
    assert run(s, 3.05, 6.0, status="no_target", phase=phase) == [(3.45, TEXT_TARGET_LOST)]


def test_the_blind_spot_does_not_say_target_lost():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, guidance=guide(hint="in_reach", aligned=True), phase="approach")
    assert run(s, 3.05, 4.5, status="no_target", phase="approach", blind=True) == []
    # Once the window is over and nothing gripped, the loss is real and is said.
    assert run(s, 4.55, 6.0, status="no_target", phase="approach") == [(4.95, TEXT_TARGET_LOST)]


def test_a_new_domain_does_not_call_the_old_target_lost():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, guidance=guide(dx=-0.2), phase="approach")
    s.forget_target()                                            # carry: the cup is out of scope, not lost
    run(s, 3.05, 6.0, status="no_target", phase="carry")
    s.forget_target()                                            # back to objects after the release
    assert run(s, 6.05, 9.0, status="no_target", phase="search") == [(6.45, TEXT_NO_TARGET)]


def test_forget_target_keeps_the_rate_limit():
    s = Summarizer(CFG)
    run(s, 0.0, 0.4, guidance=guide(dx=-0.2))                   # said at 0.4
    s.forget_target()
    assert run(s, 0.45, 3.0, status="no_target", phase="carry") == [(2.4, TEXT_FIND_DROP)]


def test_an_unknown_phase_is_a_bug():
    with pytest.raises(ValueError, match="unknown phase"):
        Summarizer(CFG).update("no_target", None, None, 0.0, phase="lunch")


# --- the label manager's notices (M4) ---

def test_a_notice_is_spoken_at_once_and_only_once():
    s = Summarizer(CFG)
    run(s, 0.0, 1.0, guidance=guide(dx=-0.2))                                  # said at 0.4
    assert s.update("ok", "cup", guide(dx=-0.2), 1.05, notices=("Learning my flask.",)) == "Learning my flask."
    assert run(s, 1.1, 10.0, guidance=guide(dx=-0.2)) == []                    # the scene did not change


def test_the_description_is_still_due_after_a_notice():
    s = Summarizer(CFG)
    assert s.update("ok", "cup", guide(dx=-0.2), 0.0, notices=("My flask is ready.",)) == "My flask is ready."
    assert run(s, 0.05, 3.0, guidance=guide(dx=-0.2)) == [(2.0, "Cup ahead, slightly left.")]  # past the rate limit


def test_a_tag_going_live_renames_the_spoken_target():
    s = Summarizer(CFG)
    run(s, 0.0, 3.0, label="bottle", guidance=guide(dx=-0.2))
    assert s.update("ok", "bottle", guide(dx=-0.2), 3.05, notices=("My flask is ready.",)) == "My flask is ready."
    assert run(s, 3.1, 6.0, label="my flask", guidance=guide(dx=-0.2)) == [(5.05, "My flask ahead, slightly left.")]


def test_events_come_before_notices_on_the_same_frame():
    s = Summarizer(CFG)
    said = s.update("no_target", None, None, 0.0, phase="carry", events=("grip",), notices=("Learning my mug.",))
    assert said == "Grip established. Learning my mug."
