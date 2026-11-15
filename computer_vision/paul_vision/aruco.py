"""The pick and drop-off stations, found by the printed ArUco markers on them.

A second detector beside YOLOE, behind the same `Detector` protocol: an oriented BGR frame in, one
`Detection` per station marker in view out, with `source="aruco"` so the tracker never mixes a marker
with an object. Print the markers with `scripts/make_markers.py`.

A station that is not there would send the operator to the wrong place, so false positives matter more
than recall. Measured on the 17,501 marker-free frames of sample_videos/ and recordings/run1.mp4:
  1. Only the two configured ids count. They were chosen as the codes furthest from what scenes produce
     (flat, half-split and striped squares). Of all 50 ids, only 17 and 37 were ever read on the clips
     (10 reads); no scene quad came within one bit of id 9, and one sliver came within one bit of id 15.
  2. Markers smaller than `min_side` are ignored: 7 of those 10 false reads were smaller than 0.03 of
     the frame, and below that size real markers stop reading reliably under blur anyway.
  3. The quad must be reasonably square (`min_side_ratio`): 42 of the 46 scene quads within two bits of
     a station code were slivers (a window edge-on, a switch plate) thinner than 0.40.
  4. OpenCV's own per-marker confidence must reach `min_confidence`, a floor for broken reads.
Bits must match exactly (`error_correction_rate: 0`); tolerating one wrong bit would have read that
sliver as the drop station, leaving only the shape check to stop it.

The box stands for the marker's size and position, not for the pixels its corners span, because the
distance hint is the box's share of the frame and `dx`, `dy` are its centre. It is a square whose side is
the mean of the marker's four sides (the length `min_side` is checked against), centred where the
marker's diagonals cross. The corners' axis-aligned bounds, used before, read a marker turned 45 degrees
in the image as twice its area, so `near` came on at about 0.50 m instead of 0.36 m. Rendered through
a 90 degree lens at four places in the frame, the square switches `near` and `in_reach` within 0.2% of
the upright marker's distance at any in-plane turn. A marker tilted away from the camera reads further
than it is (at the frame centre up to 6% at 30 degrees of tilt, 13% at 45, 22% at 60) and never more
than 3% closer. Of the other estimates tried, the square root of the quad's area read tilted markers
further still (up to 28%), and the longest side, the longest diagonal and the quad's major axis read
them closer than they are by up to 21 to 41%: the direction that announces a drop as a release.

A marker seen in a mirror is another code: the drop marker (id 15) mirrored reads as nothing, while the
pick marker (id 9) reads as itself. When `camera.flip_h` or `camera.flip_v` (one, not both) mirrors the
picture, `StationDetector(..., mirrored=True)` searches the frame a second time flipped back left to
right and keeps, per station, the better of the two reads. The second search is a second chance for a
scene quad; on the same 17,501 frames flipped left to right it read no station either.
"""
from __future__ import annotations

import cv2
import numpy as np

from .config import CameraConfig, StationsConfig
from .types import BBox, Detection

SOURCE_ARUCO = "aruco"
STATION_PICK = "pick station"
STATION_DROP = "drop station"

# OpenCV's early size filter counts contour points, and a contour that runs diagonally has about 1/sqrt(2)
# points per pixel of length. Without this slack a marker turned 45 degrees in the image would be dropped
# before it is decoded even though it is up to 1.4 times the minimum size. The exact size check runs on
# the decoded corners afterwards, so the slack only costs a few more candidates.
_CONTOUR_POINTS_PER_PIXEL = 0.7


def marker_sides(corners: np.ndarray) -> np.ndarray:
    """Lengths of the four sides of a marker, from its corners as a 4x2 array in order around the quad."""
    return np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)


def marker_centre(corners: np.ndarray) -> np.ndarray:
    """Where the diagonals of the quad cross, from its corners as a 4x2 array in order around it.

    A camera maps the printed square's diagonals onto the quad's and their crossing onto the crossing,
    so this is the image of the marker's centre under any perspective. The mean of the corners is not:
    on a steeply tilted marker close to the camera it lies tens of pixels towards the near edge. A quad
    whose diagonals are parallel has no crossing; it cannot pass the shape check, but would get the mean.
    """
    p0, p1, p2, p3 = corners
    d1, d2 = p2 - p0, p3 - p1
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) <= 1e-9 * float(d1 @ d1 + d2 @ d2):
        return corners.mean(axis=0)
    r = p1 - p0
    return p0 + d1 * ((r[0] * d2[1] - r[1] * d2[0]) / denominator)


def square_box(centre: np.ndarray, side: float, width: int, height: int) -> BBox:
    """The normalized box of a square `side` pixels wide, centred on `centre`, in a `width` x `height` image.

    `centre` is in OpenCV's pixel-centre coordinates (pixel i spans i - 0.5 to i + 0.5), so the frame
    runs from -0.5 to size - 0.5 and the same point normalizes the same at any search scale. A square that
    sticks out of the frame (only a tilted marker's can: the marker itself is inside, but foreshortened, so
    the square reaches past its near and far edges) is moved back in whole, so its area, and with it the
    distance hint, does not depend on where the marker is; the centre then moves by the overhang. Only a
    square larger than the frame is cut to it.
    """
    spans = []
    for c, size in ((float(centre[0]), width), (float(centre[1]), height)):
        lo = c + 0.5 - side / 2.0
        hi = lo + side
        if side >= size:
            lo, hi = 0.0, float(size)
        elif lo < 0.0:
            lo, hi = 0.0, side
        elif hi > size:
            lo, hi = size - side, float(size)
        spans.append((lo / size, hi / size))
    (x1, x2), (y1, y2) = spans
    return (x1, y1, x2, y2)


def mirrors_picture(camera: CameraConfig) -> bool:
    """Whether `sources.orient` mirrors the picture with this camera configuration.

    The turn (`rotate_deg`) never mirrors, and `flip_h` and `flip_v` together are a half turn, so the
    picture is mirrored exactly when one of the two is set.
    """
    return bool(camera.flip_h) != bool(camera.flip_v)


def mirror_box(bbox: BBox) -> BBox:
    """A normalized box found in the picture flipped left to right, in the picture itself.

    In pixel-centre coordinates the flip takes x to width - 1 - x, which turns `square_box`'s span
    [lo, hi) into [width - hi, width - lo), including a square moved back inside the frame.
    """
    x1, y1, x2, y2 = bbox
    return (1.0 - x2, y1, 1.0 - x1, y2)


def best_reads(first: list[Detection], second: list[Detection]) -> list[Detection]:
    """Two searches of one frame merged: per label, every read of the search whose best read is higher.

    A tie goes to `first`. Taking a whole search per label, rather than every read of both, keeps a
    marker that both searches read from being reported twice.
    """
    def best(found: list[Detection]) -> dict[str, float]:
        top: dict[str, float] = {}
        for d in found:
            top[d.label] = max(top.get(d.label, d.confidence), d.confidence)
        return top

    top_first, top_second = best(first), best(second)
    from_second = {label for label, c in top_second.items() if label not in top_first or c > top_first[label]}
    return ([d for d in first if d.label not in from_second]
            + [d for d in second if d.label in from_second])


class StationDetector:
    """Finds the configured station markers in a frame.

    `confidence` is OpenCV's own rating of the decoded marker (`detectMarkersWithConfidence`, new in
    OpenCV 5): one minus the share of the marker's cell pixels that disagree with the bits it decoded,
    so 1.0 is a pixel-perfect read and blur or glare lower it. It is reported as it is, after the
    `min_confidence` cut, rather than replaced by a constant, so the overlay and the logs show how
    cleanly a station reads. It is not a probability that the marker is real: false reads on the clips
    scored 0.77 to 0.94 and real ones 0.80 to 1.0, which is why the filters above, not this number, keep
    false stations out. Keep `min_confidence` at or above `selection.conf_lock` (0.40) so that every
    station this reports can be locked; a station at 0.9 also outscores most objects, so whoever feeds
    stations to the target selector must decide in which phases they are candidates.

    Every instance of a configured marker is returned, the way two cups give two detections. Print each
    id once.

    The box is not the marker's outline: it is a square of the marker's mean side, centred on the marker
    (see the module notes), so a marker turned in the image gives the same box, and the same distance
    hint, as an upright one. Drawn on a turned marker it covers the middle and leaves the corners out.

    `mirrored` (pass `mirrors_picture(cfg.camera)`) says the frames are mirrored: each frame is then
    searched twice, as it is and flipped left to right, with the boxes of the second search flipped back.
    Per station the search whose best read of it has the higher confidence wins, the first on a tie, so a
    marker that reads in both (id 9 does) is reported once. Without it each frame is searched once.
    """

    def __init__(self, cfg: StationsConfig, *, mirrored: bool = False):
        self._cfg = cfg
        self._mirrored = bool(mirrored)
        self._labels = {cfg.pick_id: STATION_PICK, cfg.drop_id: STATION_DROP}
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, cfg.dictionary))
        params = cv2.aruco.DetectorParameters()
        # Relative to the longer side of the searched image, like `min_side`, so it survives the downscale.
        params.minMarkerPerimeterRate = 4.0 * cfg.min_side * _CONTOUR_POINTS_PER_PIXEL
        params.errorCorrectionRate = cfg.error_correction_rate
        self._detector = cv2.aruco.ArucoDetector(dictionary, params)
        where = f"longer side at most {cfg.detect_max_px} px" if cfg.detect_max_px else "full size"
        if self._mirrored:
            where += ", and flipped back left to right (the camera settings mirror the picture)"
        self.description = (f"ArUco {cfg.dictionary}, pick id {cfg.pick_id}, drop id {cfg.drop_id}, "
                            f"searched at {where}" + ("" if cfg.enabled else " (disabled)"))

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Station markers in one oriented BGR frame, each boxed by `square_box`, normalized to the frame."""
        cfg = self._cfg
        if not cfg.enabled or image.size == 0:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        height, width = gray.shape[:2]
        longer = max(height, width)
        if cfg.detect_max_px and longer > cfg.detect_max_px:
            scale = cfg.detect_max_px / longer
            width, height = max(1, round(width * scale)), max(1, round(height * scale))
            gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
            longer = max(height, width)

        found = self._search(gray, width, height, longer)
        if not self._mirrored:
            return found
        flipped = [Detection(d.label, d.confidence, mirror_box(d.bbox), d.source)
                   for d in self._search(cv2.flip(gray, 1), width, height, longer)]
        return best_reads(found, flipped)

    def _search(self, gray: np.ndarray, width: int, height: int, longer: int) -> list[Detection]:
        """One search of a gray image `width` x `height`: the configured markers that pass every filter."""
        cfg = self._cfg
        corners, ids, confidences, _ = self._detector.detectMarkersWithConfidence(gray)
        if ids is None or len(ids) == 0:
            return []
        min_side_px = cfg.min_side * longer
        out = []
        for quad, marker_id, confidence in zip(corners, ids.ravel(), np.asarray(confidences).ravel()):
            label = self._labels.get(int(marker_id))
            if label is None or confidence < cfg.min_confidence:
                continue
            pts = quad.reshape(4, 2).astype(np.float64)
            sides = marker_sides(pts)
            if sides.mean() < min_side_px or sides.min() < cfg.min_side_ratio * sides.max():
                continue
            bbox = square_box(marker_centre(pts), float(sides.mean()), width, height)
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                out.append(Detection(label, float(confidence), bbox, SOURCE_ARUCO))
        return out
