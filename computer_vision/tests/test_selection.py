import dataclasses

from conftest import track

from paul_vision.config import SelectionConfig
from paul_vision.selection import TargetSelector

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
