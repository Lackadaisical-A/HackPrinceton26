import dataclasses

import pytest
from conftest import det

from paul_vision import tracker as tracker_module
from paul_vision.aruco import SOURCE_ARUCO, STATION_DROP, STATION_PICK
from paul_vision.config import TrackerConfig
from paul_vision.tracker import IDENTITY_SOURCES, IouTracker

CFG = TrackerConfig(iou_min=0.3, center_dist_max=0.15, ttl_s=1.0, label_memory_s=10.0, label_margin=0.2)


def test_id_is_stable_while_an_object_moves():
    tracker = IouTracker(CFG)
    ids = [tracker.update([det(cx=0.30 + 0.02 * i)], t=i / 30)[0].track_id for i in range(20)]
    assert set(ids) == {1}


def test_each_object_gets_its_own_id_and_keeps_it():
    tracker = IouTracker(CFG)
    first = tracker.update([det("cup", cx=0.2), det("box", cx=0.8)], t=0.0)
    second = tracker.update([det("box", cx=0.79), det("cup", cx=0.21)], t=0.03)  # order swapped
    assert {tr.label: tr.track_id for tr in first} == {"cup": 1, "box": 2}
    assert {tr.label: tr.track_id for tr in second} == {"cup": 1, "box": 2}


def test_output_follows_detection_order_and_carries_detection_values():
    tracker = IouTracker(CFG)
    out = tracker.update([det("box", conf=0.6, cx=0.8), det("cup", conf=0.7, cx=0.2)], t=0.0)
    assert [(tr.label, tr.confidence) for tr in out] == [("box", 0.6), ("cup", 0.7)]
    assert out[0].bbox == det(cx=0.8).bbox


def test_fast_motion_falls_back_to_centre_distance():
    tracker = IouTracker(CFG)
    a = tracker.update([det(cx=0.30, w=0.1, h=0.1)], t=0.0)[0]
    b = tracker.update([det(cx=0.42, w=0.1, h=0.1)], t=0.03)[0]  # no overlap, 0.12 away
    assert a.track_id == b.track_id


def test_a_far_jump_is_a_new_object():
    tracker = IouTracker(CFG)
    a = tracker.update([det(cx=0.2, w=0.1, h=0.1)], t=0.0)[0]
    b = tracker.update([det(cx=0.8, w=0.1, h=0.1)], t=0.03)[0]
    assert a.track_id != b.track_id


def test_id_survives_a_short_gap_but_not_a_long_one():
    tracker = IouTracker(CFG)
    assert tracker.update([det()], t=0.0)[0].track_id == 1
    assert tracker.update([], t=0.5) == []
    assert tracker.update([det()], t=0.9)[0].track_id == 1   # back within ttl_s
    assert tracker.update([], t=1.0) == []
    assert tracker.update([det()], t=2.5)[0].track_id == 2   # gone longer than ttl_s


def test_best_overlap_wins_when_two_detections_compete():
    tracker = IouTracker(CFG)
    tracker.update([det(cx=0.50)], t=0.0)
    out = tracker.update([det(cx=0.58), det(cx=0.51)], t=0.03)
    assert [tr.track_id for tr in out] == [2, 1]


def test_label_flicker_does_not_change_id_or_label():
    tracker = IouTracker(CFG)
    labels = ["bottle", "pill bottle", "bottle", "pill bottle", "bottle"]
    out = [tracker.update([det(label)], t=i * 0.05)[0] for i, label in enumerate(labels)]
    assert {tr.track_id for tr in out} == {1}
    assert {tr.label for tr in out} == {"bottle"}


def test_a_confident_view_outlasts_a_longer_doubtful_one():
    tracker = IouTracker(CFG)
    for i in range(30):                                     # 1 s seen from the side
        tracker.update([det("bottle", conf=0.9)], t=i / 30)
    labels = [tracker.update([det("cup", conf=0.45)], t=1 + i / 30)[0].label for i in range(150)]  # 5 s cap-first
    assert labels[:45] == ["bottle"] * 45                   # 1.5 s of weak "cup" does not outweigh 1 s of strong "bottle"
    assert labels[-1] == "cup"                              # ...but the label does follow in the end
    assert labels == sorted(labels)                         # and changes exactly once ("bottle" < "cup")


def test_a_wrong_first_label_is_corrected_at_once():
    tracker = IouTracker(CFG)
    assert tracker.update([det("cup", conf=0.25)], t=0.0)[0].label == "cup"
    corrected = tracker.update([det("bottle", conf=0.8)], t=0.03)[0]
    assert (corrected.track_id, corrected.label) == (1, "bottle")


def test_old_votes_fade():
    tracker = IouTracker(dataclasses.replace(CFG, label_memory_s=0.5))
    for i in range(30):
        tracker.update([det("bottle", conf=0.9)], t=i / 30)
    labels = [tracker.update([det("cup", conf=0.45)], t=1 + i / 30)[0].label for i in range(45)]
    assert labels[-1] == "cup"                              # with a 10 s memory this stays "bottle" (see above)


def test_without_memory_the_label_is_the_latest_one():
    tracker = IouTracker(dataclasses.replace(CFG, label_memory_s=0.0))
    labels = ["bottle", "cup", "cup", "bottle"]
    assert [tracker.update([det(label)], t=i * 0.05)[0].label for i, label in enumerate(labels)] == labels


def test_sources_are_never_mixed():
    tracker = IouTracker(CFG)
    yolo = tracker.update([det(source="yolo")], t=0.0)[0]
    marker = tracker.update([det(source="aruco")], t=0.03)[0]  # same place, different source
    assert yolo.track_id != marker.track_id


def test_reset_forgets_tracks_but_never_reuses_ids():
    tracker = IouTracker(CFG)
    tracker.update([det()], t=0.0)
    tracker.reset()
    assert tracker.update([det()], t=0.03)[0].track_id == 2


# --- station markers: the label is the decoded id, an identity ----------------------------------------


def marker(label: str, cx: float, cy: float = 0.5, conf: float = 0.95):
    return det(label, conf=conf, cx=cx, cy=cy, w=0.12, h=0.12 * 16 / 9, source=SOURCE_ARUCO)


def run(frames: list[list]) -> list[list]:
    """Feed one list of detections per frame at 30 FPS; return the tracks of every frame."""
    tracker = IouTracker(CFG)
    return [tracker.update(dets, t=i / 30) for i, dets in enumerate(frames)]


def assert_markers_keep_their_identity(frames: list[list], out: list[list]) -> None:
    """Every marker track reports its detection's label, and each station keeps a single track_id."""
    ids: dict[str, set[int]] = {}
    for dets, tracks in zip(frames, out):
        for d, tr in zip(dets, tracks):
            assert tr.label == d.label
            ids.setdefault(d.label, set()).add(tr.track_id)
    assert all(len(v) == 1 for v in ids.values()), ids
    assert ids[STATION_PICK].isdisjoint(ids[STATION_DROP])


def test_only_the_marker_source_has_identity_labels():
    assert IDENTITY_SOURCES == {SOURCE_ARUCO}


def test_markers_side_by_side_never_take_each_others_track():
    # The drop station is followed alone; the pick station, 0.12 to its right, comes into view on the one
    # frame the drop station is missed, close enough to continue the drop station's track by centre distance.
    frames = ([[marker(STATION_DROP, 0.50)]] * 30 + [[marker(STATION_PICK, 0.62)]]
              + [[marker(STATION_DROP, 0.50), marker(STATION_PICK, 0.62)]] * 10)
    out = run(frames)
    assert_markers_keep_their_identity(frames, out)
    assert out[30][0].track_id != out[0][0].track_id


def crossing() -> list[list]:
    # Each moves 0.03 a frame; just after they meet, each detection overlaps the other's last box more than its own.
    return [[marker(STATION_PICK, 0.30 + 0.03 * i), marker(STATION_DROP, 0.70 - 0.03 * i, cy=0.52)] for i in range(15)]


def test_crossing_markers_keep_their_tracks():
    frames = crossing()
    assert_markers_keep_their_identity(frames, run(frames))


def test_a_marker_in_the_place_of_the_other_starts_its_own_track():
    frames = [[marker(STATION_DROP, 0.5)]] * 30 + [[marker(STATION_PICK, 0.5)]] * 10 + [[marker(STATION_DROP, 0.5)]]
    out = run(frames)
    assert_markers_keep_their_identity(frames, out)
    assert out[-1][0].track_id == out[0][0].track_id  # the drop station's track was still alive (ttl_s)


@pytest.mark.parametrize("scenario", ["side by side", "crossing", "replaced"])
def test_without_the_identity_rule_markers_would_swap(monkeypatch, scenario):
    # Not vacuous: label-blind matching, as objects get, hands one station's track (and name) to the other.
    monkeypatch.setattr(tracker_module, "IDENTITY_SOURCES", frozenset())
    frames = {
        "side by side": [[marker(STATION_DROP, 0.50)]] * 30 + [[marker(STATION_PICK, 0.62)]],
        "crossing": crossing(),
        "replaced": [[marker(STATION_DROP, 0.5)]] * 30 + [[marker(STATION_PICK, 0.5)]],
    }[scenario]
    out = run(frames)
    assert any(tr.label != d.label for dets, tracks in zip(frames, out) for d, tr in zip(dets, tracks))


def test_objects_still_match_across_labels_beside_markers():
    tracker = IouTracker(CFG)
    stations = [marker(STATION_DROP, 0.5, cy=0.3), marker(STATION_PICK, 0.62, cy=0.3)]
    labels = ["cup", "bottle", "bottle", "pill bottle", "bottle"]
    confs = [0.25, 0.8, 0.8, 0.5, 0.8]
    out = [tracker.update(stations + [det(label, conf=conf, cx=0.5, cy=0.6)], t=i * 0.05)
           for i, (label, conf) in enumerate(zip(labels, confs))]
    objects = [tracks[2] for tracks in out]
    assert {tr.track_id for tr in objects} == {objects[0].track_id}       # label-blind continuation
    assert [tr.label for tr in objects] == ["cup", "bottle", "bottle", "bottle", "bottle"]  # the vote
    assert [{tr.label for tr in tracks[:2]} for tracks in out] == [{STATION_DROP, STATION_PICK}] * 5
