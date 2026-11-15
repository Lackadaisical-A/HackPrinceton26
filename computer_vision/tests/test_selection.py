import dataclasses

import pytest
from conftest import box, track

from paul_vision.config import SelectionConfig
from paul_vision.selection import TargetSelector
from paul_vision.types import Track

CFG = SelectionConfig(conf_lock=0.40, conf_keep=0.25, center_weight=1.0, switch_margin=0.25,
                      switch_dwell_s=0.5, coast_s=0.15, preferred_label=None)


def test_no_tracks_is_no_target():
    sel = TargetSelector(CFG).update([], t=0.0)
    assert (sel.status, sel.target) == ("no_target", None)


def test_only_weak_candidates_is_low_confidence():
    sel = TargetSelector(CFG).update([track(conf=0.30)], t=0.0)
    assert (sel.status, sel.target) == ("low_confidence", None)


def test_acquire_needs_conf_lock_and_reports_changed_once():
    selector = TargetSelector(CFG)
    first = selector.update([track(conf=0.45)], t=0.0)
    second = selector.update([track(conf=0.45)], t=0.03)
    assert (first.status, first.target.track_id, first.fresh, first.changed) == ("ok", 1, True, True)
    assert (second.status, second.changed) == ("ok", False)


def test_keeping_a_target_only_needs_conf_keep():
    selector = TargetSelector(CFG)
    selector.update([track(conf=0.60)], t=0.0)
    sel = selector.update([track(conf=0.30)], t=0.03)  # below conf_lock, above conf_keep
    assert (sel.status, sel.target.confidence, sel.fresh) == ("ok", 0.30, True)


def test_picks_the_most_centred_object_when_confidence_is_equal():
    sel = TargetSelector(CFG).update([track(1, cx=0.9, cy=0.9), track(2, cx=0.55), track(3, cx=0.1)], t=0.0)
    assert sel.target.track_id == 2


def test_center_weight_zero_means_confidence_only():
    cfg = dataclasses.replace(CFG, center_weight=0.0)
    sel = TargetSelector(cfg).update([track(1, conf=0.95, cx=0.95, cy=0.95), track(2, conf=0.90)], t=0.0)
    assert sel.target.track_id == 1


def test_missed_frames_coast_then_drop():
    selector = TargetSelector(CFG)
    locked = selector.update([track(cx=0.6)], t=0.0)
    coasting = selector.update([], t=0.10)
    dropped = selector.update([], t=0.20)
    assert (coasting.status, coasting.fresh, coasting.changed) == ("ok", False, False)
    assert coasting.target == locked.target  # the last seen box is held
    assert (dropped.status, dropped.target) == ("no_target", None)


def test_coast_can_be_disabled():
    selector = TargetSelector(dataclasses.replace(CFG, coast_s=0.0))
    selector.update([track()], t=0.0)
    assert selector.update([], t=0.03).status == "no_target"


def test_confidence_below_conf_keep_counts_as_lost():
    selector = TargetSelector(CFG)
    selector.update([track(conf=0.60)], t=0.0)
    assert selector.update([track(conf=0.20)], t=0.05).status == "ok"              # coasting
    assert selector.update([track(conf=0.20)], t=0.30).status == "low_confidence"  # dropped


def test_reacquiring_after_a_drop_reports_changed():
    selector = TargetSelector(CFG)
    selector.update([track()], t=0.0)
    selector.update([], t=1.0)
    sel = selector.update([track()], t=1.03)
    assert (sel.status, sel.target.track_id, sel.changed) == ("ok", 1, True)


def test_a_slightly_better_rival_never_takes_over():
    selector = TargetSelector(CFG)
    selector.update([track(1, conf=0.60)], t=0.0)
    for i in range(1, 60):  # two seconds of a rival that is only 10% better
        sel = selector.update([track(1, conf=0.60), track(2, conf=0.66)], t=i / 30)
        assert sel.target.track_id == 1


def test_a_clearly_better_rival_takes_over_after_the_dwell_time():
    selector = TargetSelector(CFG)
    selector.update([track(1, conf=0.50, cx=0.8)], t=0.0)
    scene = [track(1, conf=0.50, cx=0.8), track(2, conf=0.90, cx=0.5)]
    early = selector.update(scene, t=0.1)   # rival first seen
    still = selector.update(scene, t=0.5)   # 0.4 s of dwell, not enough
    switched = selector.update(scene, t=0.65)
    after = selector.update(scene, t=0.70)
    assert (early.target.track_id, still.target.track_id) == (1, 1)
    assert (switched.target.track_id, switched.changed) == (2, True)
    assert (after.target.track_id, after.changed) == (2, False)


def test_rival_dwell_restarts_if_the_rival_goes_away():
    selector = TargetSelector(CFG)
    current, rival = track(1, conf=0.50, cx=0.8), track(2, conf=0.90, cx=0.5)
    selector.update([current], t=0.0)
    selector.update([current, rival], t=0.1)
    selector.update([current], t=0.4)          # rival vanishes: the dwell clock restarts
    selector.update([current, rival], t=0.5)
    assert selector.update([current, rival], t=0.9).target.track_id == 1
    assert selector.update([current, rival], t=1.05).target.track_id == 2


def test_preferred_label_restricts_the_choice():
    selector = TargetSelector(dataclasses.replace(CFG, preferred_label="box"))
    scene = [track(1, "cup", conf=0.95), track(2, "box", conf=0.50, cx=0.8)]
    assert selector.update(scene, t=0.0).target.track_id == 2
    assert selector.update([track(1, "cup", conf=0.95)], t=1.0).status == "no_target"


def test_setting_a_preferred_label_drops_a_target_with_another_label():
    selector = TargetSelector(CFG)
    scene = [track(1, "cup", conf=0.95), track(2, "box", conf=0.50, cx=0.8)]
    assert selector.update(scene, t=0.0).target.label == "cup"
    selector.set_preferred_label("box")
    sel = selector.update(scene, t=0.03)
    assert (sel.target.label, sel.changed) == ("box", True)
    selector.set_preferred_label(None)
    assert selector.update(scene, t=0.06).target.label == "box"  # clearing does not drop the target


# --- what may be a target: objects, or the drop station (M2) ---

def station(track_id: int = 50, label: str = "drop station", conf: float = 0.95, cx: float = 0.5, cy: float = 0.5,
            w: float = 0.2, h: float = 0.2) -> Track:
    return Track(track_id, label, conf, box(cx, cy, w, h), "aruco")


def test_objects_are_the_default_domain():
    assert TargetSelector(CFG).domain == "objects"


def test_a_station_marker_is_never_an_object_target():
    # Even the most confident, best centred marker: outside carry/place the stations are drawn, never targeted.
    selector = TargetSelector(CFG)
    scene = [station(conf=0.99), station(51, "pick station", conf=0.99), track(1, "cup", conf=0.5, cx=0.8)]
    assert selector.update(scene, t=0.0).target.track_id == 1
    assert selector.update([station(conf=0.99)], t=1.0).status == "no_target"  # not even low_confidence


def test_only_the_drop_station_is_a_target_in_the_station_domain():
    selector = TargetSelector(CFG)
    selector.set_domain("station")
    scene = [track(1, "cup", conf=0.99), station(51, "pick station", conf=0.99), station(50, conf=0.80, cx=0.8)]
    sel = selector.update(scene, t=0.0)
    assert (sel.status, sel.target.track_id, sel.target.source) == ("ok", 50, "aruco")


def test_objects_and_the_pick_station_count_for_nothing_in_the_station_domain():
    # A cup in view while carrying is no reason for "low_confidence": the drop station is what is looked for.
    selector = TargetSelector(CFG)
    selector.set_domain("station")
    sel = selector.update([track(1, "cup", conf=0.3), track(2, "cup"), station(51, "pick station")], t=0.0)
    assert (sel.status, sel.target) == ("no_target", None)
    assert selector.update([station(conf=0.3)], t=1.0).status == "low_confidence"


def test_a_label_that_merely_reads_drop_station_is_not_the_marker():
    selector = TargetSelector(CFG)
    selector.set_domain("station")
    assert selector.update([track(1, "drop station")], t=0.0).status == "no_target"  # source yolo


def test_changing_the_domain_drops_the_target_at_once():
    # No coasting across the change: the old target must never be published under the new phase.
    selector = TargetSelector(CFG)
    selector.update([track(1, "cup")], t=0.0)
    assert selector.set_domain("station") is True
    sel = selector.update([track(1, "cup")], t=0.03)
    assert (sel.status, sel.target) == ("no_target", None)
    selector.update([station()], t=0.06)
    assert selector.set_domain("objects") is True
    sel = selector.update([station()], t=0.09)
    assert (sel.status, sel.target) == ("no_target", None)


def test_a_domain_change_reselects_on_the_same_frame():
    selector = TargetSelector(CFG)
    scene = [track(1, "cup"), station()]
    assert selector.update(scene, t=0.0).target.track_id == 1
    selector.set_domain("station")
    sel = selector.update(scene, t=0.03)
    assert (sel.target.track_id, sel.changed) == (50, True)


def test_setting_the_same_domain_keeps_the_target():
    selector = TargetSelector(CFG)
    selector.update([track(1, "cup")], t=0.0)
    assert selector.set_domain("objects") is False
    assert selector.update([], t=0.03).status == "ok"  # still coasting on the cup


def test_an_unknown_domain_is_refused():
    with pytest.raises(ValueError, match="unknown target domain"):
        TargetSelector(CFG).set_domain("pick")


def test_the_preferred_label_applies_to_objects_only():
    selector = TargetSelector(dataclasses.replace(CFG, preferred_label="box"))
    selector.set_domain("station")
    assert selector.update([station()], t=0.0).target.label == "drop station"
    selector.set_preferred_label("cup")      # select_target during carry: kept for later, the station stays
    assert selector.update([station()], t=0.03).target.label == "drop station"
    selector.set_domain("objects")
    scene = [track(1, "box", conf=0.99), track(2, "cup", conf=0.5, cx=0.8)]
    assert selector.update(scene, t=0.06).target.label == "cup"
