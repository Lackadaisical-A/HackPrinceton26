"""Find the wrist camera:  uv run python scripts/list_cameras.py

Opens camera indices 0-5 the same way the pipeline does and saves one picture from each to
snapshots/camN.jpg. Look at the pictures, then set camera.wrist_index in config.yaml.
"""
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paul_vision.config import load_config  # noqa: E402
from paul_vision.sources import fourcc_name, open_capture  # noqa: E402

MEASURE_S = 2.0


def main() -> int:
    cfg = load_config().camera
    out_dir = Path(__file__).resolve().parent.parent / "snapshots"
    out_dir.mkdir(exist_ok=True)
    found = 0
    for index in range(6):
        cap = open_capture(index, cfg)
        if cap is None:
            continue
        ok, frame = False, None
        for _ in range(8):  # the first frames are often dark while auto-exposure settles
            ok, frame = cap.read()
        if ok:
            found += 1
            count, started = 0, time.perf_counter()
            while time.perf_counter() - started < MEASURE_S:  # the rate the camera reports is often not the real one
                count += cap.read()[0]
            path = out_dir / f"cam{index}.jpg"
            cv2.imwrite(str(path), frame)
            marker = "  <- camera.wrist_index" if index == cfg.wrist_index else ""
            print(f"index {index}: {frame.shape[1]}x{frame.shape[0]} {fourcc_name(cap) or '?'}, "
                  f"measured {count / MEASURE_S:.0f} fps -> {path}{marker}")
        else:
            print(f"index {index}: opened but gave no frame")
        cap.release()
    if not found:
        print("no working camera found")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
