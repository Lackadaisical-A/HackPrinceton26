"""scripts/make_markers.py: the sheet it draws, for every dictionary size the config accepts."""
import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_markers.py"
_spec = importlib.util.spec_from_file_location("make_markers", _SCRIPT)
make_markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_markers)


@pytest.mark.parametrize("name, marker_id, cells", [("DICT_4X4_50", 15, 6), ("DICT_5X5_100", 42, 7),
                                                     ("DICT_6X6_250", 9, 8), ("DICT_7X7_1000", 999, 9)])
@pytest.mark.parametrize("size_mm, dpi", [(100, 300), (70, 150)])
def test_every_cell_is_whole_pixels_and_the_square_is_the_asked_size(name, marker_id, cells, size_mm, dpi):
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
    assert make_markers.marker_cells(dictionary) == cells
    img, actual_mm = make_markers.sheet(name, marker_id, "drop station", size_mm, dpi)
    side = round(actual_mm / make_markers.MM_PER_INCH * dpi)
    cell, rest = divmod(side, cells)
    assert rest == 0
    assert abs(actual_mm - size_mm) <= 0.5 * cell / dpi * make_markers.MM_PER_INCH  # the nearest whole cell
    quiet = make_markers.QUIET_CELLS * cell
    square = img[quiet:quiet + side, quiet:quiet + side]
    # Pure black and white, so no edge is resampled into grey. (OpenCV's nearest-neighbour upscale may still
    # put a cell edge one pixel off, which is why this samples the cell centres rather than every pixel.)
    assert set(np.unique(square).tolist()) == {0, 255}
    bits = cv2.aruco.generateImageMarker(dictionary, marker_id, cells, borderBits=1)  # one pixel per cell
    assert np.array_equal(square[cell // 2::cell, cell // 2::cell], bits)
    assert (img[quiet - 1, quiet:quiet + side] == 255).all() and (img[quiet:quiet + side, quiet + side] == 255).all()
    # And it reads as itself.
    small = cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters()).detectMarkers(small)
    assert ids is not None and ids.ravel().tolist() == [marker_id]
