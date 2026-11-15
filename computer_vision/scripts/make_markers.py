"""Write print-ready PNGs of the two station markers configured in config.yaml (`stations`).

    uv run python scripts/make_markers.py                  # markers/pick_station.png, markers/drop_station.png
    uv run python scripts/make_markers.py --size-mm 150    # bigger, for a camera that sees the table from further

Print each sheet at 100 % ("actual size", not "fit to page") and check with a ruler that the black square
measures what its caption says. Keep the white margin around the square when trimming: the detector needs
that light quiet zone to find the black border. Mount the sheet flat and matte; a curl or a glare spot
across the code costs more than a smaller print.

Size. The default black square is 100 mm (a 167 x 229 mm sheet; fits A4 and Letter). On synthetic
1280x720 frames a marker was found in at least 9 frames out of 10 once its side covered 12 px sharp and
face-on, 34-36 px with 2 px of blur or 8 px of motion blur, and 44 px at 60 degrees off face-on or with
tilt, blur and dim light together; markers under 38 px (stations.min_side) are ignored. The sizes below
allow 60 px at the far end, and at the near end a black square no taller than 600 of the 720 rows.
The wrist camera's field of view has not been measured; the default assumes 90 degrees horizontal:

    horizontal FOV    found reliably out to     whole in the frame down to
    60 degrees        18 x the side             1.9 x the side   -> print 70 mm instead
    90 degrees        11 x the side (1.1 m)     1.1 x the side (11 cm)
    120 degrees        6 x the side (0.6 m)     0.6 x the side

Place each marker where the camera sees it within about 60 degrees of face-on (on an upright or tilted
card rather than flat on the table, if the camera looks across the table): more slanted than that it
reads poorly, and stations.min_side_ratio drops it. Fast swings blur any marker (16 px of motion blur
needed 136 px); the station is found again as the arm slows down.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paul_vision.aruco import STATION_DROP, STATION_PICK  # noqa: E402
from paul_vision.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402

RECOMMENDED_MM = 100
QUIET_CELLS = 2        # white margin on every side of the black square, in marker cells (see marker_cells)
MM_PER_INCH = 25.4


def png_with_dpi(png: bytes, dpi: int) -> bytes:
    """Insert a pHYs chunk after IHDR, so printing at 100 % gives the intended physical size.

    OpenCV does not write the resolution, and without it a printer guesses (often 72 or 96 dpi).
    """
    pixels_per_metre = round(dpi / MM_PER_INCH * 1000)
    data = struct.pack(">IIB", pixels_per_metre, pixels_per_metre, 1)  # unit 1 = metre
    chunk = struct.pack(">I", len(data)) + b"pHYs" + data + struct.pack(">I", zlib.crc32(b"pHYs" + data))
    end_of_ihdr = 8 + 4 + 4 + 13 + 4  # PNG signature, then IHDR's length, type, 13 data bytes and CRC
    return png[:end_of_ihdr] + chunk + png[end_of_ihdr:]


def put_centred(img: np.ndarray, text: str, y: int, height_px: int, thickness: int) -> None:
    """Draw one line of text centred at baseline y, at the given cap height or smaller if it would not fit."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (base_w, base_h), _ = cv2.getTextSize(text, font, 1.0, thickness)
    scale = min(height_px / base_h, 0.92 * img.shape[1] / base_w)
    (w, _), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.putText(img, text, ((img.shape[1] - w) // 2, y), font, scale, 0, thickness, cv2.LINE_AA)


def marker_cells(dictionary: cv2.aruco.Dictionary) -> int:
    """Cells across the black square: the code's own (`markerSize`, 4 in DICT_4X4) plus a border cell each side."""
    return dictionary.markerSize + 2


def sheet(dictionary_name: str, marker_id: int, station: str, size_mm: float, dpi: int) -> tuple[np.ndarray, float]:
    """One marker with its quiet zone and a caption underneath. Returns the image and the true square size."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
    cells = marker_cells(dictionary)
    cell = max(4, round(size_mm / cells / MM_PER_INCH * dpi))  # whole pixels per cell keeps every cell edge sharp
    side = cells * cell
    actual_mm = side / dpi * MM_PER_INCH
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, side, borderBits=1)
    quiet = QUIET_CELLS * cell
    width = side + 2 * quiet
    line = max(12, round(cell * 0.45))  # caption cap height
    caption = np.full((round(line * 8.2), width), 255, np.uint8)
    thick = max(1, line // 8)
    put_centred(caption, station.upper(), round(line * 1.6), round(line * 1.3), thick * 2)
    put_centred(caption, f"{dictionary_name}  id {marker_id}", round(line * 3.6), line, thick)
    put_centred(caption, f"black square {actual_mm:.0f} mm", round(line * 5.4), round(line * 0.8), thick)
    put_centred(caption, "print at 100 %, keep the white margin", round(line * 6.8), round(line * 0.8), thick)
    img = np.vstack([cv2.copyMakeBorder(marker, quiet, quiet, quiet, quiet, cv2.BORDER_CONSTANT, value=255),
                     caption])
    cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), 200, max(1, cell // 20))  # cut line
    return img, actual_mm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     epilog=__doc__.split("\n\n", 1)[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config.yaml with the `stations` ids")
    parser.add_argument("--out", default=str(DEFAULT_CONFIG_PATH.parent / "markers"), help="folder for the PNGs")
    parser.add_argument("--size-mm", type=float, default=RECOMMENDED_MM,
                        help=f"side of the black square in millimetres (default {RECOMMENDED_MM})")
    parser.add_argument("--dpi", type=int, default=300, help="print resolution written into the PNG (default 300)")
    args = parser.parse_args()
    if args.size_mm <= 0 or args.dpi <= 0:
        parser.error("--size-mm and --dpi must be positive")

    stations = load_config(args.config).stations
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for station, marker_id in ((STATION_PICK, stations.pick_id), (STATION_DROP, stations.drop_id)):
        img, actual_mm = sheet(stations.dictionary, marker_id, station, args.size_mm, args.dpi)
        ok, png = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError("PNG encoding failed")
        path = out / f"{station.replace(' ', '_')}.png"
        path.write_bytes(png_with_dpi(png.tobytes(), args.dpi))
        sheet_mm = (img.shape[1] / args.dpi * MM_PER_INCH, img.shape[0] / args.dpi * MM_PER_INCH)
        print(f"{path}   {station}, {stations.dictionary} id {marker_id}, black square {actual_mm:.1f} mm, "
              f"sheet {sheet_mm[0]:.0f} x {sheet_mm[1]:.0f} mm at {args.dpi} dpi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
