import numpy as np
import pytest

from paul_vision.geometry import area, center, center_distance, clamp, iou
from paul_vision.guidance import raw_offset
from paul_vision.sources import orient


def test_center_and_area():
    assert center((0.2, 0.4, 0.6, 0.8)) == pytest.approx((0.4, 0.6))
    assert area((0.2, 0.4, 0.6, 0.8)) == pytest.approx(0.16)
    assert area((0.6, 0.4, 0.2, 0.8)) == 0.0  # inverted box


def test_iou():
    a = (0.0, 0.0, 0.5, 0.5)
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, (0.5, 0.5, 1.0, 1.0)) == 0.0
    assert iou(a, (0.25, 0.0, 0.75, 0.5)) == pytest.approx(1 / 3)
    assert iou((0.1, 0.1, 0.1, 0.1), (0.1, 0.1, 0.1, 0.1)) == 0.0  # degenerate boxes


def test_center_distance_and_clamp():
    assert center_distance((0.0, 0.0, 0.2, 0.2), (0.3, 0.4, 0.5, 0.6)) == pytest.approx(0.5)
    assert (clamp(-2, -1, 1), clamp(0.3, -1, 1), clamp(5, -1, 1)) == (-1, 0.3, 1)


# --- orientation: where does an object end up, and which way does guidance point? ---

def _scene():
    """A 4x6 (h x w) image with one bright pixel near the top-right corner: row 0, column 5."""
    image = np.zeros((4, 6, 3), np.uint8)
    image[0, 5] = 255
    return image


def _bright_pixel(image):
    rows, cols = np.nonzero(image[:, :, 0])
    return int(rows[0]), int(cols[0])


@pytest.mark.parametrize("rotate, flip_h, flip_v, shape, pixel", [
    (0, False, False, (4, 6), (0, 5)),
    (90, False, False, (6, 4), (5, 3)),    # clockwise: top-right goes to bottom-right
    (180, False, False, (4, 6), (3, 0)),
    (270, False, False, (6, 4), (0, 0)),   # counter-clockwise: top-right goes to top-left
    (0, True, False, (4, 6), (0, 0)),
    (0, False, True, (4, 6), (3, 5)),
    (0, True, True, (4, 6), (3, 0)),       # both flips = 180 degrees
    (90, True, False, (6, 4), (5, 0)),     # rotation first, then the mirror
])
def test_orient_moves_pixels_as_documented(rotate, flip_h, flip_v, shape, pixel):
    out = orient(_scene(), rotate, flip_h, flip_v)
    assert out.shape[:2] == shape
    assert _bright_pixel(out) == pixel


def test_orient_identity_returns_the_same_array():
    image = _scene()
    assert orient(image, 0, False, False) is image


@pytest.mark.parametrize("rotate, flip_h, flip_v, sign_dx, sign_dy", [
    (0, False, False, +1, -1),   # object top-right in the raw image: guidance says right and up
    (180, False, False, -1, +1),  # camera mounted upside down: it is really left and down
    (90, False, False, +1, +1),
    (270, False, False, -1, -1),
    (0, True, False, -1, -1),
    (0, False, True, +1, +1),
])
def test_offsets_follow_the_oriented_frame(rotate, flip_h, flip_v, sign_dx, sign_dy):
    out = orient(_scene(), rotate, flip_h, flip_v)
    row, col = _bright_pixel(out)
    height, width = out.shape[:2]
    bbox = (col / width, row / height, (col + 1) / width, (row + 1) / height)
    dx, dy = raw_offset(bbox)
    assert (np.sign(dx), np.sign(dy)) == (sign_dx, sign_dy)
