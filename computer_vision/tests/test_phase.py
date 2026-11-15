"""The phase machine: every transition, and the rules it must keep (see the docstring of paul_vision.phase)."""
import dataclasses
import itertools
import random

import pytest

from paul_vision.config import PHASE_HINT_ORDER, ConfigError, PhaseConfig, validate_phase_config
from paul_vision.guidance import DISTANCE_HINTS
from paul_vision.phase import (ALERT_DROPPED, DOMAIN_OBJECTS, DOMAIN_STATION, EVENT_DROPPED, EVENT_GRIP,
                               EVENT_RELEASED, EVENT_SENSOR_LOST, PHASES, PhaseMachine, target_domain)
from paul_vision.pressure import Contact

CFG = PhaseConfig(grasp_settle_s=0.5, release_hold_s=1.0, place_hint="near", place_dwell_s=0.5,
                  leave_place_hint="far", leave_place_dwell_s=1.0, alert_hold_s=1.0, sensor_loss_hold_s=2.0,
                  blind_spot_hint="in_reach", blind_spot_s=1.5)
DT = 0.0625  # 16 frames per second: a power of two, so frame times and their differences are exact

HOLD = Contact(True, 0.7, 0.0)        # the pads say holding
OPEN = Contact(False, 0.02, 0.0)      # the pads say not holding
DECIDING = Contact(None, 0.2, 0.0)    # fresh readings that have not decided yet
STALE = Contact(None, None, 0.0)      # no fresh reading: the feed is gone
NO_FEED = None                        # no pressure feed configured at all


class Run:
    """Drives a PhaseMachine frame by frame on a 16 FPS clock and keeps every step."""

    def __init__(self, cfg: PhaseConfig = CFG):
        self.m = PhaseMachine(cfg)
        self.i = 0
        self.steps = []

    @property
    def t(self) -> float:
        return self.i * DT

    def frames(self, seconds: float, contact=NO_FEED, hint=None):
        """Run for `seconds` (at least one frame) with the same contact and hint; returns the last step."""
        for _ in range(max(1, round(seconds / DT))):
            self.i += 1
            self.steps.append(self.m.step(self.t, contact, hint))
        return self.steps[-1]

    def frame(self, contact=NO_FEED, hint=None):
        return self.frames(DT, contact, hint)

    def force(self, phase: str, contact=NO_FEED, hint=None):
        self.m.force(phase)
        return self.frame(contact, hint)

    @property
    def phase(self) -> str:
        return self.m.phase

    def events(self, since: int = 0) -> list[str]:
        return [e for s in self.steps[since:] for e in s.events]

    def phases(self, since: int = 0) -> set[str]:
        return {s.phase for s in self.steps[since:]}

    def to_carry(self, hint=None) -> None:
        """search -> grasp -> carry on pad evidence."""
        self.frames(CFG.grasp_settle_s + 2 * DT, HOLD, hint)
        assert self.phase == "carry"

    def to_place(self) -> None:
        self.to_carry()
        self.frames(CFG.place_dwell_s + 2 * DT, HOLD, "near")
        assert self.phase == "place"


# --- the configuration -------------------------------------------------------------------------------------------

def test_the_config_knows_the_distance_hints_in_order():
    assert PHASE_HINT_ORDER == DISTANCE_HINTS


def test_the_test_config_is_valid():
    validate_phase_config(CFG)


@pytest.mark.parametrize("changes, message", [
    (dict(place_hint="close"), "place_hint"),
    (dict(blind_spot_hint="touching"), "blind_spot_hint"),
    (dict(leave_place_hint="near"), "further"),
    (dict(leave_place_hint="in_reach"), "further"),
    (dict(grasp_settle_s=-0.1), "grasp_settle_s"),
    (dict(sensor_loss_hold_s=-1), "sensor_loss_hold_s"),
    (dict(alert_hold_s=0), "alert_hold_s"),
])
def test_phase_config_ranges(changes, message):
    with pytest.raises(ConfigError, match=message):
        validate_phase_config(dataclasses.replace(CFG, **changes))


# --- the target domain -------------------------------------------------------------------------------------------

def test_the_drop_station_is_the_target_in_carry_and_place_and_objects_otherwise():
    assert {p: target_domain(p) for p in PHASES} == {
        "search": DOMAIN_OBJECTS, "approach": DOMAIN_OBJECTS, "grasp": DOMAIN_OBJECTS,
        "carry": DOMAIN_STATION, "place": DOMAIN_STATION, "release": DOMAIN_OBJECTS}


@pytest.mark.parametrize("phase, contact, hint", list(itertools.product(
    PHASES, [HOLD, OPEN, DECIDING, STALE, NO_FEED], [None, *DISTANCE_HINTS])))
def test_the_vision_step_never_changes_the_target_domain(phase, contact, hint):
    # The target was chosen in the domain fixed by update_contact; a domain change after it would publish a
    # carry with an object, or a search with a station, for one frame.
    m = PhaseMachine(CFG)
    m.force(phase)
    m.update_contact(0.0, contact)
    for i in range(1, 60):
        before = target_domain(m.update_contact(i * DT, contact))
        assert target_domain(m.update_vision(i * DT, hint).phase) == before


# --- invariant 1: no pad data, no pad phases ---------------------------------------------------------------------

@pytest.mark.parametrize("contact", [NO_FEED, STALE])
def test_without_pad_data_the_phase_is_exactly_milestone_1(contact):
    # M1: approach while a target is locked, else search. No events, no alert, no blind spot.
    rng = random.Random(7)
    run = Run()
    for _ in range(2000):
        hint = rng.choice([None, None, *DISTANCE_HINTS])
        step = run.frame(contact, hint)
        assert step.phase == ("approach" if hint is not None else "search")
        assert step.events == () and step.alert is None and not step.blind


@pytest.mark.parametrize("contact", [NO_FEED, STALE, DECIDING])
def test_unknown_contact_never_enters_grasp_or_release_and_never_raises_dropped(contact):
    run = Run()
    run.frames(10, contact, "in_reach")
    run.frames(10, contact, None)
    assert run.phases() <= {"search", "approach"}
    assert run.events() == [] and all(s.alert is None for s in run.steps)


@pytest.mark.parametrize("contact", [NO_FEED, STALE, DECIDING])
def test_unknown_contact_in_carry_or_place_is_neither_a_drop_nor_a_release(contact):
    for phase in ("carry", "place"):
        run = Run()
        run.to_carry() if phase == "carry" else run.to_place()
        start = len(run.steps)
        run.frames(1.5, contact, None)  # shorter than sensor_loss_hold_s
        assert run.phases(start) == {phase}
        assert run.events(start) == [] and all(s.alert is None for s in run.steps[start:])


# --- the transitions ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("hint", [None, "in_reach"])
def test_contact_starts_a_grasp_from_search_or_approach(hint):
    run = Run()
    assert run.frame(NO_FEED, hint).phase == ("approach" if hint else "search")
    assert run.frame(HOLD, hint).phase == "grasp"


def test_a_grasp_becomes_a_carry_once_contact_has_held_for_the_settle_time():
    run = Run()
    run.frame(HOLD)
    grasp_t = run.t
    while run.phase == "grasp":
        run.frame(HOLD)
    assert run.phase == "carry"
    assert run.t - grasp_t == pytest.approx(CFG.grasp_settle_s)
    assert run.events() == [EVENT_GRIP]  # said once, at the change
    assert run.steps[-1].events == (EVENT_GRIP,)
    run.frames(3, HOLD)
    assert run.events() == [EVENT_GRIP]


def test_a_wavering_grip_restarts_the_settle_time():
    run = Run()
    run.frames(0.3, HOLD)
    run.frame(DECIDING)  # not a release, but not a hold either
    run.frames(0.3, HOLD)
    assert run.phase == "grasp"
    run.frames(0.25, HOLD)
    assert run.phase == "carry"


@pytest.mark.parametrize("hint, back_to", [(None, "search"), ("in_reach", "approach")])
def test_a_failed_grasp_goes_back_quietly(hint, back_to):
    run = Run()
    run.frames(0.2, HOLD, hint)
    assert run.phase == "grasp"
    step = run.frame(OPEN, hint)
    assert step.phase == back_to
    assert run.events() == [] and all(s.alert is None for s in run.steps)


def test_a_failed_grasp_can_be_tried_again():
    run = Run()
    run.frames(0.2, HOLD)
    run.frame(OPEN)
    assert run.frame(HOLD).phase == "grasp"


def test_carry_becomes_place_when_the_drop_station_is_near_for_the_dwell_time():
    run = Run()
    run.to_carry()
    run.frames(2, HOLD, "far")
    assert run.phase == "carry"
    run.frames(CFG.place_dwell_s, HOLD, "near")  # the dwell starts on the first of these frames
    assert run.phase == "carry"
    run.frame(HOLD, "near")
    assert run.phase == "place"
    assert run.events() == [EVENT_GRIP]  # place says nothing by itself


def test_in_reach_counts_as_near_for_place():
    run = Run()
    run.to_carry()
    run.frames(CFG.place_dwell_s + DT, HOLD, "in_reach")
    assert run.phase == "place"


@pytest.mark.parametrize("interruption", [None, "far"])
def test_the_place_dwell_starts_again_after_an_interruption(interruption):
    run = Run()
    run.to_carry()
    run.frames(0.4, HOLD, "near")
    run.frame(HOLD, interruption)
    run.frames(CFG.place_dwell_s, HOLD, "near")
    assert run.phase == "carry"
    run.frame(HOLD, "near")
    assert run.phase == "place"


def test_place_is_sticky_when_the_station_goes_out_of_view():
    # Close up, the held object covers the marker; that must not undo place.
    run = Run()
    run.to_place()
    run.frames(10, HOLD, None)
    assert run.phase == "place"


def test_place_survives_a_brief_far_reading():
    run = Run()
    run.to_place()
    for _ in range(5):
        run.frames(0.9, HOLD, "far")
        run.frame(HOLD, None)
    assert run.phase == "place"


def test_place_goes_back_to_carry_when_the_station_is_clearly_further_away_for_the_dwell():
    run = Run()
    run.to_place()
    run.frames(CFG.leave_place_dwell_s, HOLD, "far")
    assert run.phase == "place"
    run.frame(HOLD, "far")
    assert run.phase == "carry"
    assert run.events() == [EVENT_GRIP]


def test_letting_go_at_the_drop_station_is_a_release_not_a_drop():
    run = Run()
    run.to_place()
    step = run.frame(OPEN, None)
    assert step.phase == "release" and step.events == (EVENT_RELEASED,) and step.alert is None
    assert all(s.alert is None for s in run.steps)


def test_release_returns_to_search_after_the_hold_time():
    run = Run()
    run.to_place()
    run.frame(OPEN)
    released_t = run.t
    while run.phase == "release":
        run.frame(OPEN)
    assert run.t - released_t == pytest.approx(CFG.release_hold_s)
    assert run.phase == "search"
    run.frames(3, OPEN)
    assert run.events() == [EVENT_GRIP, EVENT_RELEASED]


def test_release_then_an_object_in_view_is_approach():
    run = Run()
    run.to_place()
    run.frames(CFG.release_hold_s + DT, OPEN, "near")
    assert run.phase == "approach"


def test_gripping_again_during_release_is_a_new_grasp():
    run = Run()
    run.to_place()
    run.frames(0.3, OPEN)
    assert run.frame(HOLD).phase == "grasp"


def test_letting_go_in_carry_is_a_drop_with_an_alert_held_for_alert_hold_s():
    run = Run()
    run.to_carry()
    run.frames(1, HOLD, "far")
    start = len(run.steps)
    step = run.frame(OPEN, None)  # no object in view
    assert step.phase == "search"
    assert step.events == (EVENT_DROPPED,)
    run.frames(2, OPEN)
    alerts = [s.alert for s in run.steps[start:]]
    held = [a for a in alerts if a == ALERT_DROPPED]
    assert alerts[:len(held)] == held  # consecutive, from the drop on
    assert len(held) == round(CFG.alert_hold_s / DT)
    assert run.events(start) == [EVENT_DROPPED]


def test_the_alert_is_dropped_even_with_an_object_in_view():
    run = Run()
    run.to_carry()
    step = run.frame(OPEN, "near")
    assert step.phase == "approach" and step.alert == ALERT_DROPPED


# --- invariant 2: a drop needs a hold, then a release ------------------------------------------------------------

def test_stale_data_then_open_is_still_a_drop():
    run = Run()
    run.to_carry()
    run.frames(0.5, STALE)
    step = run.frame(OPEN)
    assert step.events == (EVENT_DROPPED,) and step.alert == ALERT_DROPPED


def test_stale_data_alone_is_never_a_drop():
    run = Run()
    run.to_carry()
    run.frames(10, STALE)
    assert run.phase == "search"
    assert run.events() == [EVENT_GRIP, EVENT_SENSOR_LOST]
    assert all(s.alert is None for s in run.steps)


def test_open_without_a_hold_in_carry_is_not_a_drop():
    run = Run()
    run.force("carry", OPEN)
    run.frames(3, OPEN)
    assert run.phase == "carry" and run.events() == [] and all(s.alert is None for s in run.steps)


# --- invariant 3: set_phase sticks -------------------------------------------------------------------------------

def test_a_forced_phase_takes_effect_on_the_next_frame_and_is_marked():
    run = Run()
    run.m.force("carry")
    assert run.phase == "search"
    assert run.frame().phase == "carry"
    assert run.m.forced


def test_an_unknown_phase_cannot_be_forced():
    with pytest.raises(ValueError, match="unknown phase"):
        PhaseMachine(CFG).force("lunch")


def test_forcing_search_while_the_pads_hold_does_not_bounce_to_grasp():
    run = Run()
    run.to_carry()
    start = len(run.steps)
    run.force("search", HOLD, "in_reach")
    run.frames(5, HOLD, "in_reach")
    assert run.phases(start) == {"approach"}
    # New evidence moves it on as usual: the grip opens, then closes again.
    run.frames(0.2, OPEN, "in_reach")
    assert run.frame(HOLD, "in_reach").phase == "grasp"


def test_forcing_search_while_the_pads_hold_then_unknown_then_hold_still_does_not_grasp():
    # Unknown is not a change of state; only a different known state re-arms the pad transitions.
    run = Run()
    run.force("search", HOLD)
    run.frames(0.5, STALE)
    run.frames(1, HOLD)
    assert run.phase == "search"


@pytest.mark.parametrize("unknown", [DECIDING, STALE, NO_FEED])
def test_forcing_search_while_the_pads_are_unknown_then_hold_does_not_grasp(unknown):
    # Holding all along, but undecided on the frame of the command (fresh again just after a gap, say): the first
    # state the pads decide on is the one present at the command, not news.
    run = Run()
    run.to_carry()
    start = len(run.steps)
    run.force("search", unknown, "in_reach")
    run.frames(3, HOLD, "in_reach")
    assert run.phases(start) == {"approach"} and EVENT_GRIP not in run.events(start)
    # A real change after that still acts: the grip opens, then closes again.
    run.frames(0.2, OPEN, "in_reach")
    assert run.frame(HOLD, "in_reach").phase == "grasp"


def test_forcing_search_while_the_pads_are_unknown_then_open_then_hold_grasps():
    run = Run()
    run.force("search", DECIDING)
    run.frames(1, OPEN)               # the first decided state: the baseline
    assert run.phase == "search"
    assert run.frame(HOLD).phase == "grasp"  # a change from it is news


def test_forcing_release_while_the_pads_are_unknown_then_hold_does_not_bounce_to_grasp():
    run = Run()
    run.to_carry()
    start = len(run.steps)
    run.force("release", DECIDING)
    run.frames(3, HOLD)
    assert run.phase == "search" and "grasp" not in run.phases(start)
    run.frames(0.2, OPEN)
    assert run.frame(HOLD).phase == "grasp"


def test_forcing_grasp_while_the_pads_are_unknown_then_open_is_not_a_failed_grasp():
    run = Run()
    run.force("grasp", DECIDING)
    run.frames(3, OPEN)               # the first decided state: the baseline
    assert run.phases() == {"grasp"} and run.events() == []
    run.frames(CFG.grasp_settle_s + DT, HOLD)
    assert run.phase == "carry" and run.events() == [EVENT_GRIP]


@pytest.mark.parametrize("first", [HOLD, OPEN])
def test_forcing_carry_while_the_pads_are_unknown_then_a_hold_and_a_let_go_is_a_drop(first):
    run = Run()
    run.force("carry", DECIDING)
    run.frames(1, first)              # the first decided state: the baseline
    assert run.phases() == {"carry"} and run.events() == []
    run.frames(1, HOLD)
    step = run.frame(OPEN)            # a real change after the baseline still acts
    assert step.events == (EVENT_DROPPED,) and step.alert == ALERT_DROPPED


@pytest.mark.parametrize("contact", [NO_FEED, STALE, OPEN, DECIDING])
def test_forcing_carry_without_contact_holds_and_raises_nothing(contact):
    run = Run()
    run.force("carry", contact)
    run.frames(10, contact, "far")  # longer than sensor_loss_hold_s
    assert run.phases() == {"carry"}
    assert run.events() == [] and all(s.alert is None for s in run.steps)


def test_a_forced_carry_without_pad_data_targets_the_station_and_reaches_place_from_the_marker():
    run = Run()
    run.force("carry")
    assert target_domain(run.phase) == DOMAIN_STATION
    run.frames(3, NO_FEED, "far")
    run.frames(CFG.place_dwell_s + DT, NO_FEED, "near")
    assert run.phase == "place"
    run.frames(10, NO_FEED, None)
    assert run.phase == "place"  # no pad data: no release, no fallback
    run.frames(CFG.leave_place_dwell_s + DT, NO_FEED, "far")
    assert run.phase == "carry"


def test_a_forced_carry_with_a_hold_then_open_is_a_drop():
    run = Run()
    run.force("carry", HOLD)
    run.frames(1, HOLD)
    step = run.frame(OPEN)
    assert step.events == (EVENT_DROPPED,) and step.alert == ALERT_DROPPED


def test_a_forced_carry_while_open_then_a_hold_then_open_is_a_drop():
    run = Run()
    run.force("carry", OPEN)
    run.frames(1, HOLD)
    assert run.phase == "carry"
    assert run.frame(OPEN).events == (EVENT_DROPPED,)


def test_a_forced_grasp_with_a_hold_settles_into_carry():
    run = Run()
    run.force("grasp", HOLD)
    run.frames(0.5, HOLD)
    assert run.phase == "carry" and run.events() == [EVENT_GRIP]
    assert not run.m.forced  # the pads have confirmed it


def test_a_forced_grasp_without_pad_data_holds():
    run = Run()
    run.force("grasp")
    run.frames(10)
    assert run.phases() == {"grasp"} and run.events() == []


def test_a_forced_place_far_from_the_station_waits_until_it_has_been_close():
    # The operator may say "place" before the marker is close; leaving place needs it seen close first.
    run = Run()
    run.force("place", HOLD)
    run.frames(3, HOLD, "far")
    assert run.phase == "place"
    run.frame(HOLD, "near")
    run.frames(CFG.leave_place_dwell_s + DT, HOLD, "far")
    assert run.phase == "carry"


def test_a_forced_release_while_the_pads_hold_does_not_bounce_to_grasp():
    run = Run()
    run.to_carry()
    start = len(run.steps)
    run.force("release", HOLD)
    run.frames(3, HOLD)
    assert run.phase == "search"  # release ran its hold time, then the object stage
    assert "grasp" not in run.phases(start)
    run.frames(0.2, OPEN)
    assert run.frame(HOLD).phase == "grasp"


@pytest.mark.parametrize("phase", PHASES)
def test_a_forced_phase_is_kept_on_its_first_frame_whatever_the_pads_say(phase):
    # (search and approach are one stage, named by the lock: with nothing locked it is search.)
    expected = "search" if phase == "approach" else phase
    for contact in (HOLD, OPEN, DECIDING, STALE, NO_FEED):
        run = Run()
        run.to_carry()
        assert run.force(phase, contact).phase == expected


def test_a_pending_set_phase_survives_a_reset():
    m = PhaseMachine(CFG)
    m.force("carry")
    m.reset()  # a camera switch at the same moment
    assert m.step(1.0, None, None).phase == "carry"


# --- invariant 4: the pad feed dies ------------------------------------------------------------------------------

@pytest.mark.parametrize("phase", ["grasp", "carry", "place"])
@pytest.mark.parametrize("gone", [STALE, NO_FEED])
def test_a_dead_pad_feed_holds_the_phase_then_falls_back_to_vision_only(phase, gone):
    run = Run()
    if phase == "grasp":
        run.frames(0.2, HOLD)
    elif phase == "carry":
        run.to_carry()
    else:
        run.to_place()
    assert run.phase == phase
    start = len(run.steps)
    run.frames(CFG.sensor_loss_hold_s, gone)  # the last of these is one frame short of the hold
    assert run.phases(start) == {phase}
    step = run.frame(gone)
    assert step.phase == "search" and step.events == (EVENT_SENSOR_LOST,) and step.alert is None
    run.frames(5, gone)
    assert run.phases(len(run.steps) - 10) == {"search"}
    assert run.events(start) == [EVENT_SENSOR_LOST]


def test_a_pad_feed_back_within_the_hold_changes_nothing():
    run = Run()
    run.to_carry()
    run.frames(1.5, STALE)
    run.frames(3, HOLD)
    assert run.phase == "carry" and run.events() == [EVENT_GRIP]


def test_a_pad_feed_back_within_the_hold_saying_open_is_a_drop():
    run = Run()
    run.to_carry()
    run.frames(1.5, STALE)
    assert run.frame(OPEN).events == (EVENT_DROPPED,)


def test_a_pad_feed_back_after_the_fallback_is_a_new_start():
    run = Run()
    run.to_carry()
    run.frames(3, STALE)
    assert run.phase == "search"
    run.frames(CFG.grasp_settle_s + DT, HOLD)
    assert run.phase == "carry"
    assert run.events() == [EVENT_GRIP, EVENT_SENSOR_LOST, EVENT_GRIP]


def test_fresh_undecided_readings_are_not_a_dead_feed():
    run = Run()
    run.to_carry()
    run.frames(10, DECIDING)
    assert run.phase == "carry" and run.events() == [EVENT_GRIP]


# --- invariant 5: a camera stall is not a drop -------------------------------------------------------------------

def test_a_camera_hiccup_while_carrying_keeps_the_carry():
    # The pipeline stops calling the machine while no frame arrives, then calls reset_vision().
    run = Run()
    run.to_carry()
    run.frames(0.5, HOLD, "far")
    run.i += round(1.0 / DT)  # one second without frames
    run.m.reset_vision()
    start = len(run.steps)
    run.frames(1, HOLD, None)
    assert run.phases(start) == {"carry"} and run.events(start) == []


def test_a_camera_hiccup_resets_the_place_dwell():
    run = Run()
    run.to_carry()
    run.frames(0.4, HOLD, "near")
    run.i += round(1.0 / DT)
    run.m.reset_vision()
    run.frames(CFG.place_dwell_s, HOLD, "near")
    assert run.phase == "carry"  # the dwell counts only what was seen after the hiccup
    run.frame(HOLD, "near")
    assert run.phase == "place"


def test_a_camera_switch_while_holding_is_back_in_carry_after_the_settle_time():
    run = Run()
    run.to_carry()
    run.m.reset()  # a different camera: everything starts over
    start = len(run.steps)
    run.frames(CFG.grasp_settle_s + DT, HOLD)
    assert [s.phase for s in run.steps[start:]][:2] == ["grasp", "grasp"]
    assert run.phase == "carry"
    assert all(s.alert is None for s in run.steps)


# --- the grasp blind spot ----------------------------------------------------------------------------------------

def test_a_target_lost_in_reach_keeps_approach_while_the_gripper_closes():
    run = Run()
    run.frames(1, OPEN, "in_reach")
    start = len(run.steps)
    run.frames(CFG.blind_spot_s, OPEN, None)
    assert all(s.phase == "approach" and s.blind for s in run.steps[start:])
    step = run.frame(OPEN, None)
    assert step.phase == "search" and not step.blind
    assert run.events() == []


def test_the_blind_spot_ends_in_a_grasp_when_the_pads_close():
    run = Run()
    run.frames(1, OPEN, "in_reach")
    run.frames(0.5, OPEN, None)
    assert run.steps[-1].blind
    step = run.frame(HOLD, None)
    assert step.phase == "grasp" and not step.blind


@pytest.mark.parametrize("contact", [NO_FEED, STALE])
def test_no_blind_spot_without_pad_data(contact):
    run = Run()
    run.frames(1, contact, "in_reach")
    step = run.frame(contact, None)
    assert step.phase == "search" and not step.blind


def test_no_blind_spot_for_a_target_lost_further_away():
    run = Run()
    run.frames(1, OPEN, "near")
    step = run.frame(OPEN, None)
    assert step.phase == "search" and not step.blind


def test_a_target_seen_further_away_again_ends_the_blind_spot_promise():
    run = Run()
    run.frames(1, OPEN, "in_reach")
    run.frame(OPEN, "near")
    assert run.frame(OPEN, None).phase == "search"


def test_blind_spot_s_zero_turns_it_off():
    run = Run(dataclasses.replace(CFG, blind_spot_s=0.0))
    run.frames(1, OPEN, "in_reach")
    assert run.frame(OPEN, None).phase == "search"


def test_the_pad_feed_dying_during_the_blind_spot_ends_it():
    run = Run()
    run.frames(1, OPEN, "in_reach")
    run.frame(OPEN, None)
    step = run.frame(STALE, None)
    assert step.phase == "search" and not step.blind
