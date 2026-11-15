"""StationDetector on synthetic frames: markers drawn with OpenCV's own generator, warped into a 1280x720 frame."""
import copy
import dataclasses

import cv2
import numpy as np
import pytest
import yaml

from paul_vision.aruco import (SOURCE_ARUCO, STATION_DROP, STATION_PICK, StationDetector, best_reads, marker_centre,
                               mirror_box, mirrors_picture, square_box)
from paul_vision.config import (DEFAULT_CONFIG_PATH, ConfigError, DistanceConfig, DistanceThresholds,
                                GuidanceConfig, StationsConfig, aruco_dictionary_size, from_dict)
from paul_vision.guidance import GuidanceFilter
from paul_vision.sources import orient
from paul_vision.types import Detection, Track

W, H = 1280, 720
CFG = StationsConfig(enabled=True, dictionary="DICT_4X4_50", pick_id=9, drop_id=15, min_side=0.03,
                     min_side_ratio=0.40, detect_max_px=1280, min_confidence=0.75, error_correction_rate=0.0)
DICTIONARY = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
CELL = 20  # source pixels per marker cell before warping


def with_cfg(**changes) -> StationsConfig:
    return StationsConfig(**{**CFG.__dict__, **changes})


def frame(gray: int = 150) -> np.ndarray:
    return np.full((H, W, 3), gray, np.uint8)


def square(cx: float, cy: float, side: float, angle_deg: float = 0.0) -> np.ndarray:
    """Corners of a square in the image, clockwise from top-left, turned in the image plane."""
    a = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    unit = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]]) * side
    return unit @ rot.T + (cx, cy)


def tilted(cx: float, cy: float, side: float, tilt_deg: float, focal: float = W / 2) -> np.ndarray:
    """Corners of a square card tipped back by tilt_deg, seen by a pinhole camera (focal 640 px = 90 degree HFOV).

    The card sits at the depth where an untilted one would measure `side` pixels, so tilt only foreshortens it.
    """
    t = np.deg2rad(tilt_deg)
    pts = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]]) * side
    x, y, z = pts[:, 0], pts[:, 1] * np.cos(t), focal + pts[:, 1] * np.sin(t)
    return np.stack([cx + focal * x / z, cy + focal * y / z], axis=1)


def draw(img: np.ndarray, marker_id: int, corners: np.ndarray, quiet_cells: float = 1.5) -> np.ndarray:
    """Warp a printed marker (black square plus white margin) so its black square lands on `corners`.

    `corners` are in frame pixels measured from the frame's top-left edge, the units a normalized box times
    the frame size is in. OpenCV's warp counts from pixel centres instead, so both sides shift by half a pixel.
    """
    marker = cv2.aruco.generateImageMarker(DICTIONARY, marker_id, 6 * CELL, borderBits=1)
    q = round(quiet_cells * CELL)
    sheet = cv2.copyMakeBorder(marker, q, q, q, q, cv2.BORDER_CONSTANT, value=255)
    sheet = cv2.cvtColor((sheet.astype(np.float32) * 0.8 + 25).astype(np.uint8), cv2.COLOR_GRAY2BGR)  # paper, ink
    src = np.float32([[q, q], [q + 6 * CELL, q], [q + 6 * CELL, q + 6 * CELL], [q, q + 6 * CELL]]) - 0.5
    hom = cv2.getPerspectiveTransform(src, np.float32(corners) - 0.5)
    warped = cv2.warpPerspective(sheet, hom, (img.shape[1], img.shape[0]), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.ones(sheet.shape[:2], np.float32), hom, (img.shape[1], img.shape[0]))[..., None]
    img[:] = (img * (1 - mask) + warped * mask).astype(np.uint8)
    return img


def mean_side(corners: np.ndarray) -> float:
    return float(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1).mean())


def bounds_area(corners: np.ndarray) -> float:
    """Share of the frame covered by the corners' axis-aligned bounds: what the box used to be."""
    return float(np.prod(np.ptp(corners, axis=0)) / (W * H))


def assert_box_on(bbox, corners: np.ndarray, centre: tuple[float, float], px: float) -> None:
    """The normalized box is a square as wide as the drawn marker's mean side, centred on `centre`, to `px` pixels."""
    x1, y1, x2, y2 = np.array(bbox) * (W, H, W, H)
    got = np.array([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1])
    side = mean_side(corners)
    expected = np.array([centre[0], centre[1], side, side])
    assert np.abs(got - expected).max() <= px, (got.round(1), expected.round(1))


GUIDANCE = GuidanceConfig(smoothing_tau_s=0.3, deadzone_enter=0.08, deadzone_exit=0.12)
DISTANCE = DistanceConfig(hysteresis=0.15, thresholds={"default": DistanceThresholds(near=0.05, in_reach=0.2),
                                                       STATION_DROP: DistanceThresholds(near=0.03, in_reach=0.12)})


def test_satisfies_the_detector_protocol():
    det = StationDetector(CFG)
    assert isinstance(det.description, str) and "DICT_4X4_50" in det.description
    assert det.detect(frame()) == []


@pytest.mark.parametrize("marker_id, label", [(CFG.pick_id, STATION_PICK), (CFG.drop_id, STATION_DROP)])
def test_each_configured_id_gets_its_station_label(marker_id, label):
    found = StationDetector(CFG).detect(draw(frame(), marker_id, square(640, 360, 120)))
    assert [(d.label, d.source) for d in found] == [(label, SOURCE_ARUCO)]
    assert CFG.min_confidence <= found[0].confidence <= 1.0


def test_ids_that_are_not_configured_are_ignored():
    img = draw(frame(), 4, square(400, 360, 120))
    draw(img, 0, square(900, 360, 120))
    assert StationDetector(CFG).detect(img) == []
    # Not vacuous: the same markers are found once their ids are the configured ones.
    found = StationDetector(with_cfg(pick_id=4, drop_id=0)).detect(img)
    assert sorted(d.label for d in found) == [STATION_DROP, STATION_PICK]


@pytest.mark.parametrize("corners, centre", [
    (square(300, 200, 150), (300, 200)),
    (square(900, 450, 140, angle_deg=30), (900, 450)),
    (tilted(640, 360, 200, tilt_deg=40), (640, 360)),
], ids=["upright", "turned", "tilted"])
def test_box_is_a_square_of_the_marker_centred_on_it(corners, centre):
    found = StationDetector(CFG).detect(draw(frame(), CFG.pick_id, corners))
    assert len(found) == 1
    assert_box_on(found[0].bbox, corners, centre, px=2)


@pytest.mark.parametrize("cx, cy", [(640, 360), (330, 520)])
def test_turning_a_marker_in_the_image_does_not_change_its_box(cx, cy):
    det = StationDetector(CFG)
    boxes = {}
    for angle in range(0, 91, 15):
        (found,) = det.detect(draw(frame(), CFG.drop_id, square(cx, cy, 160, angle_deg=angle)))
        boxes[angle] = np.array(found.bbox) * (W, H, W, H)
    upright = boxes[0]
    for angle, box in boxes.items():
        size, centre = box[2:] - box[:2], (box[:2] + box[2:]) / 2
        assert np.prod(size) == pytest.approx(np.prod(upright[2:] - upright[:2]), rel=0.02), angle
        assert np.abs(centre - (upright[:2] + upright[2:]) / 2).max() <= 1.0, angle
    # What the axis-aligned bounds of the corners, the box before, made of the same marker at 45 degrees:
    assert bounds_area(square(cx, cy, 160, angle_deg=45)) == pytest.approx(2 * bounds_area(square(cx, cy, 160)))


@pytest.mark.parametrize("side, hint", [(160, "far"), (200, "near")])
def test_a_marker_turned_45_degrees_gives_the_same_distance_hint_as_an_upright_one(side, hint):
    # Drop-station thresholds of 0.03 (near) and 0.12 (in_reach). A 160 px marker fills 0.028 of the frame.
    det = StationDetector(CFG)
    for angle in (0, 45):
        corners = square(900, 300, side, angle_deg=angle)
        (found,) = det.detect(draw(frame(), CFG.drop_id, corners))
        track = Track(1, found.label, found.confidence, found.bbox, found.source)
        guidance = GuidanceFilter(GUIDANCE, DISTANCE).update(track, t=0.0)
        assert guidance.distance_hint == hint, angle
        assert (guidance.dx, guidance.dy) == pytest.approx((900 / W * 2 - 1, 300 / H * 2 - 1), abs=2 / H)
    # Not vacuous: the old box, the corners' bounds, read the turned 160 px marker as near.
    assert bounds_area(square(900, 300, 160, angle_deg=45)) > DISTANCE.thresholds[STATION_DROP].near


def test_box_centre_is_the_marker_centre_under_perspective():
    corners = tilted(640, 360, 300, tilt_deg=50)  # the printed centre lands on (640, 360)
    (found,) = StationDetector(CFG).detect(draw(frame(), CFG.drop_id, corners))
    x1, y1, x2, y2 = np.array(found.bbox) * (W, H, W, H)
    assert ((x1 + x2) / 2, (y1 + y2) / 2) == pytest.approx((640, 360), abs=1.0)
    assert corners.mean(axis=0)[1] < 360 - 15  # the corners' mean, by contrast, sits well above it


def test_box_is_in_frame_coordinates_when_the_search_is_downscaled():
    corners = square(900, 250, 160, angle_deg=15)
    found = StationDetector(with_cfg(detect_max_px=640)).detect(draw(frame(), CFG.drop_id, corners))
    assert len(found) == 1
    assert_box_on(found[0].bbox, corners, (900, 250), px=4)  # one pixel of the half-size search is two of the frame


def test_box_near_the_frame_edge_stays_inside_it():
    corners = square(W - 50, H - 50, 70)  # the sheet's white margin runs off the frame
    found = StationDetector(CFG).detect(draw(frame(), CFG.pick_id, corners))
    assert len(found) == 1
    x1, y1, x2, y2 = found[0].bbox
    assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1


def test_a_tilted_marker_at_the_top_edge_keeps_its_size():
    # Tilted 60 degrees, the marker is about 100 px tall but 155 px across on average, so its square reaches
    # past the top of the frame; it is moved down whole rather than cut, keeping the distance hint.
    corners = tilted(640, 64, 200, tilt_deg=60)
    assert corners[:, 1].min() > 0 and 64 - mean_side(corners) / 2 < 0
    (found,) = StationDetector(CFG).detect(draw(frame(), CFG.drop_id, corners, quiet_cells=0.5))
    x1, y1, x2, y2 = np.array(found.bbox) * (W, H, W, H)
    assert y1 == 0.0
    assert (x2 - x1, y2 - y1) == pytest.approx((mean_side(corners), mean_side(corners)), abs=2.0)
    assert (x1 + x2) / 2 == pytest.approx(640, abs=1.0)


def test_square_box_in_pixel_centre_coordinates():
    # OpenCV puts pixel i's centre at i, so the frame's middle is 639.5, 359.5 and its edges are at -0.5.
    assert square_box(np.array([639.5, 359.5]), 100.0, W, H) == pytest.approx(
        ((640 - 50) / W, (360 - 50) / H, (640 + 50) / W, (360 + 50) / H))
    # Sticking out of the frame: moved back in whole.
    assert square_box(np.array([10.0, 700.0]), 100.0, W, H) == pytest.approx((0.0, 620 / H, 100 / W, 1.0))
    assert square_box(np.array([1275.0, 5.0]), 100.0, W, H) == pytest.approx((1180 / W, 0.0, 1.0, 100 / H))
    # Larger than the frame on one axis: cut to it on that axis only.
    assert square_box(np.array([639.5, 359.5]), 800.0, W, H) == pytest.approx((240 / W, 0.0, 1040 / W, 1.0))


def test_marker_centre_is_where_the_diagonals_cross():
    assert marker_centre(square(300, 200, 150, angle_deg=30)) == pytest.approx((300, 200))
    seen = tilted(640, 360, 300, tilt_deg=50)
    assert marker_centre(seen) == pytest.approx((640, 360))
    assert abs(seen.mean(axis=0)[1] - 360) > 15
    flat = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])  # no crossing: the mean, not an error
    assert marker_centre(flat) == pytest.approx((1.5, 0.0))


def test_markers_smaller_than_min_side_are_ignored():
    det = StationDetector(with_cfg(min_side=0.05))  # 64 px on a 1280 px wide frame
    assert det.detect(draw(frame(), CFG.pick_id, square(640, 360, 56))) == []
    assert len(det.detect(draw(frame(), CFG.pick_id, square(640, 360, 72)))) == 1


def test_a_marker_turned_45_degrees_just_above_min_side_is_found():
    # OpenCV's early size filter counts contour points, which run short on diagonal edges; see
    # _CONTOUR_POINTS_PER_PIXEL. Without that slack this marker is dropped before it is decoded.
    det = StationDetector(with_cfg(min_side=0.05))
    found = det.detect(draw(frame(), CFG.pick_id, square(640, 360, 70, angle_deg=45)))
    assert [d.label for d in found] == [STATION_PICK]


def test_the_size_limit_follows_the_downscale():
    # min_side is a fraction of the frame, so a downscaled search must keep and drop the same markers.
    det = StationDetector(with_cfg(min_side=0.05, detect_max_px=640))
    assert det.detect(draw(frame(), CFG.pick_id, square(640, 360, 56))) == []
    assert len(det.detect(draw(frame(), CFG.pick_id, square(640, 360, 80)))) == 1


def test_thin_slivers_are_rejected():
    # A marker squashed to 0.3 of its width still decodes; only the shape check stops it.
    sliver = np.array([[560, 340], [720, 340], [720, 388], [560, 388]], np.float64)
    img = draw(frame(), CFG.pick_id, sliver)
    assert StationDetector(CFG).detect(img) == []
    assert len(StationDetector(with_cfg(min_side_ratio=0.0)).detect(img)) == 1


def test_two_markers_at_once():
    pick, drop = square(320, 360, 130, angle_deg=-10), square(960, 300, 110, angle_deg=20)
    img = draw(draw(frame(), CFG.pick_id, pick), CFG.drop_id, drop)
    found = {d.label: d for d in StationDetector(CFG).detect(img)}
    assert set(found) == {STATION_PICK, STATION_DROP}
    assert_box_on(found[STATION_PICK].bbox, pick, (320, 360), px=2)
    assert_box_on(found[STATION_DROP].bbox, drop, (960, 300), px=2)


@pytest.mark.parametrize("img", [frame(0), frame(150), frame(255), np.zeros((0, 0, 3), np.uint8)],
                         ids=["black", "gray", "white", "empty"])
def test_frames_without_markers_give_nothing(img):
    assert StationDetector(CFG).detect(img) == []


def test_moderate_tilt_blur_and_dim_light():
    img = draw(frame(170), CFG.drop_id, tilted(700, 380, 110, tilt_deg=45))
    img = cv2.GaussianBlur(img, (0, 0), 1.5)
    img = (img * 0.5).astype(np.uint8)
    found = StationDetector(CFG).detect(img)
    assert [d.label for d in found] == [STATION_DROP]
    assert found[0].confidence >= CFG.min_confidence


def test_reads_below_min_confidence_are_dropped():
    img = cv2.GaussianBlur(draw(frame(), CFG.pick_id, square(640, 360, 60)), (0, 0), 2.0)
    (seen,) = StationDetector(with_cfg(min_confidence=0.0)).detect(img)
    assert seen.confidence < 1.0
    assert StationDetector(with_cfg(min_confidence=seen.confidence + 0.001)).detect(img) == []


def test_disabled_detector_reports_nothing():
    det = StationDetector(with_cfg(enabled=False))
    assert det.detect(draw(frame(), CFG.pick_id, square(640, 360, 150))) == []
    assert "disabled" in det.description


# --- a mirrored picture (camera.flip_h or camera.flip_v, not both) -----------------------------------------


class CountingSearch:
    """Stands in for the OpenCV detector inside a StationDetector and counts the searches."""

    def __init__(self, detector):
        self._detector, self.calls = detector, 0

    def detectMarkersWithConfidence(self, image):
        self.calls += 1
        return self._detector.detectMarkersWithConfidence(image)


def counted(det: StationDetector) -> CountingSearch:
    det._detector = CountingSearch(det._detector)
    return det._detector


def camera(shipped_cfg, flip_h: bool, flip_v: bool, rotate_deg: int = 0):
    return dataclasses.replace(shipped_cfg.camera, flip_h=flip_h, flip_v=flip_v, rotate_deg=rotate_deg)


def stations_through_orient(flip_h: bool, flip_v: bool):
    """Both markers as the camera sees them, oriented the way sources.orient does it, and their corners after it."""
    pick, drop = square(320, 360, 130, angle_deg=-10), square(960, 300, 110, angle_deg=20)
    img = orient(draw(draw(frame(), CFG.pick_id, pick), CFG.drop_id, drop), 0, flip_h, flip_v)

    def moved(corners: np.ndarray) -> np.ndarray:  # in edge coordinates a flip takes x to W - x, y to H - y
        return np.stack([W - corners[:, 0] if flip_h else corners[:, 0],
                         H - corners[:, 1] if flip_v else corners[:, 1]], axis=1)

    return img, {STATION_PICK: moved(pick), STATION_DROP: moved(drop)}


FLIPS = pytest.mark.parametrize("flip_h, flip_v", [(True, False), (False, True), (True, True), (False, False)],
                                ids=["flip_h", "flip_v", "both", "none"])


@FLIPS
@pytest.mark.parametrize("rotate_deg", [0, 90, 180, 270])
def test_mirrors_picture_agrees_with_orient(shipped_cfg, flip_h, flip_v, rotate_deg):
    pattern = np.arange(6, dtype=np.uint8).reshape(2, 3)  # no turn of it equals its mirror image
    oriented = orient(pattern, rotate_deg, flip_h, flip_v)
    turned = any(np.array_equal(oriented, np.rot90(pattern, k)) for k in range(4))
    assert mirrors_picture(camera(shipped_cfg, flip_h, flip_v, rotate_deg)) == (not turned) == (flip_h != flip_v)


@FLIPS
def test_both_stations_are_found_where_they_are_whatever_the_flips(shipped_cfg, flip_h, flip_v):
    img, corners = stations_through_orient(flip_h, flip_v)
    mirrored = mirrors_picture(camera(shipped_cfg, flip_h, flip_v))
    det = StationDetector(CFG, mirrored=mirrored)
    searches = counted(det)
    found = det.detect(img)
    assert sorted(d.label for d in found) == [STATION_DROP, STATION_PICK]  # each once
    by_label = {d.label: d for d in found}
    for label, quad in corners.items():
        assert_box_on(by_label[label].bbox, quad, tuple(quad.mean(axis=0)), px=2)
        assert by_label[label].confidence >= CFG.min_confidence
    assert searches.calls == (2 if mirrored else 1)


@pytest.mark.parametrize("flip_h, flip_v", [(True, False), (False, True)], ids=["flip_h", "flip_v"])
def test_searched_once_a_mirrored_picture_loses_the_drop_station(flip_h, flip_v):
    # What the second search is for. The pick marker (id 9) reads as itself in a mirror; the drop marker does not.
    img, _ = stations_through_orient(flip_h, flip_v)
    assert [d.label for d in StationDetector(CFG).detect(img)] == [STATION_PICK]


def test_a_mirrored_picture_is_searched_twice_also_when_downscaled():
    corners = square(900, 250, 160, angle_deg=15)
    img = cv2.flip(draw(frame(), CFG.drop_id, corners), 1)
    det = StationDetector(with_cfg(detect_max_px=640), mirrored=True)
    searches = counted(det)
    (found,) = det.detect(img)
    assert found.label == STATION_DROP and searches.calls == 2
    assert_box_on(found.bbox, corners, (W - 900, 250), px=4)


def test_an_unmirrored_detector_searches_once_and_says_nothing_of_flips(shipped_cfg):
    assert not mirrors_picture(shipped_cfg.camera)  # the shipped camera settings do not mirror
    det = StationDetector(CFG)
    searches = counted(det)
    pick = square(640, 360, 150)
    assert [d.label for d in det.detect(draw(frame(), CFG.pick_id, pick))] == [STATION_PICK]
    assert det.detect(frame()) == []
    assert searches.calls == 2  # one per frame
    assert "flipped" not in det.description
    assert "flipped back" in StationDetector(CFG, mirrored=True).description


def test_a_disabled_mirrored_detector_does_not_search():
    det = StationDetector(with_cfg(enabled=False), mirrored=True)
    searches = counted(det)
    assert det.detect(cv2.flip(draw(frame(), CFG.drop_id, square(640, 360, 150)), 1)) == []
    assert searches.calls == 0


def test_mirror_box_is_the_square_of_the_mirrored_centre():
    assert mirror_box((0.1, 0.2, 0.3, 0.4)) == pytest.approx((0.7, 0.2, 0.9, 0.4))
    # In pixel-centre coordinates the flip takes x to W - 1 - x; squares moved back inside the frame included.
    for cx, cy, side in [(100.0, 300.0, 80.0), (10.0, 700.0, 100.0), (1275.0, 5.0, 100.0), (639.5, 359.5, 800.0)]:
        box = square_box(np.array([cx, cy]), side, W, H)
        assert mirror_box(box) == pytest.approx(square_box(np.array([W - 1 - cx, cy]), side, W, H))


def test_best_reads_takes_per_station_the_search_with_the_better_read():
    def read(label, confidence, x):
        return Detection(label, confidence, (x, 0.1, x + 0.1, 0.2), SOURCE_ARUCO)

    first = [read(STATION_PICK, 0.90, 0.1), read(STATION_DROP, 0.80, 0.5)]
    second = [read(STATION_PICK, 0.95, 0.1), read(STATION_PICK, 0.70, 0.7), read(STATION_DROP, 0.80, 0.5)]
    merged = best_reads(first, second)
    # pick: the second search has the better read, so both of its picks; drop: a tie, so the first search's.
    assert merged == [first[1], second[0], second[1]]
    assert best_reads(first, []) == first
    assert best_reads([], second) == second
    assert best_reads([first[0]], [second[2]]) == [first[0], second[2]]


# --- config -------------------------------------------------------------------------------------------


@pytest.fixture
def raw():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_shipped_stations_config_builds_a_detector(shipped_cfg):
    det = StationDetector(shipped_cfg.stations)
    assert det.detect(frame()) == []


@pytest.mark.parametrize("change, message", [
    (dict(dictionary="DICT_4X4_51"), "stations.dictionary"),
    (dict(dictionary="DICT_3X3_50"), "stations.dictionary"),
    (dict(dictionary="APRILTAG_36h11"), "stations.dictionary"),
    (dict(pick_id=50), "stations.pick_id"),
    (dict(drop_id=-1), "stations.pick_id"),
    (dict(pick_id=7, drop_id=7), "must differ"),
    (dict(min_side=0.0), "stations.min_side "),
    (dict(min_side=0.6), "stations.min_side "),
    (dict(min_side_ratio=1.5), "stations.min_side_ratio"),
    (dict(detect_max_px=100), "stations.detect_max_px"),
    (dict(detect_max_px=-1), "stations.detect_max_px"),
    (dict(min_confidence=1.2), "stations.min_confidence"),
    (dict(error_correction_rate=-0.1), "stations.error_correction_rate"),
    (dict(enabled="yes"), "config.stations.enabled"),
    (dict(pick_id=9.0), "config.stations.pick_id"),
    (dict(min_size=0.1), r"config\.stations: unknown key"),
])
def test_stations_config_validation(raw, change, message):
    broken = copy.deepcopy(raw)
    broken["stations"].update(change)
    with pytest.raises(ConfigError, match=message):
        from_dict(broken)


def test_missing_stations_key_is_an_error(raw):
    del raw["stations"]["drop_id"]
    with pytest.raises(ConfigError, match=r"config\.stations: missing key\(s\) \['drop_id'\]"):
        from_dict(raw)


def test_ids_up_to_the_dictionary_size_are_accepted(raw):
    raw["stations"].update(dictionary="DICT_5X5_100", pick_id=0, drop_id=99)
    assert from_dict(raw).stations.drop_id == 99


@pytest.mark.parametrize("name", [f"DICT_{n}X{n}_{count}" for n in (4, 5, 6, 7) for count in (50, 100, 250, 1000)])
def test_every_accepted_dictionary_exists_in_opencv_with_that_many_markers(name):
    # The config is checked without OpenCV, by name; this keeps that parser honest.
    assert cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name)).bytesList.shape[0] \
        == aruco_dictionary_size(name)
