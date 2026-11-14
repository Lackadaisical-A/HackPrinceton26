"""Run the pipeline over recorded clips and report what it would have told the operator.

    uv run python scripts/eval_clips.py sample_videos                 # every clip in a folder
    uv run python scripts/eval_clips.py clip.mp4 --sheets snapshots   # also save annotated stills

Every frame is processed and time comes from the clip, not the wall clock, so the report does not
depend on how fast this machine is: the same clips and config give the same numbers. Run it before
and after changing a threshold or the class list. Clips are used as they are (camera.rotate_deg and
the flips are not applied). To watch one live instead: python -m paul_vision --source <clip>.
"""
import argparse
import dataclasses
import logging
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paul_vision import overlay  # noqa: E402
from paul_vision.app import Pipeline  # noqa: E402
from paul_vision.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from paul_vision.detector import create_detector  # noqa: E402
from paul_vision.messages import validate_vision  # noqa: E402
from paul_vision.types import STATUS_OK, Frame  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
SHEET_TILES, SHEET_COLUMNS, TILE_HEIGHT = 12, 4, 270


def clips_in(paths: list[str]) -> list[Path]:
    found = []
    for path in map(Path, paths):
        if path.is_dir():
            found += sorted(p for p in path.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
        else:
            found.append(path)
    return found


def shares(counter: Counter, total: int) -> str:
    return "   ".join(f"{name} {100 * count / total:.0f}%" for name, count in counter.most_common()) or "-"


def evaluate(path: Path, cfg, detector, sheet_dir: Path | None) -> dict | None:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or cfg.camera.fps
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    wanted = {int(i * (total - 1) / (SHEET_TILES - 1)) for i in range(SHEET_TILES)} if sheet_dir else set()

    pipeline = Pipeline(cfg, detector)
    statuses, target_labels = Counter(), Counter()
    seen = defaultdict(list)          # label -> confidence, for the best detection of each frame
    locks: list[list] = []            # [track_id, label, first_t, last_t, last frame number]
    said, tiles, detect_ms = [], [], []
    last_summary, frames, renamed = None, 0, 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        t = frames / fps
        frames += 1
        height, width = image.shape[:2]
        result = pipeline.process(Frame(image, t, frames))
        msg = result.message
        validate_vision(msg)
        detect_ms.append(1000 * pipeline.detect_s)
        statuses[msg["status"]] += 1
        if result.tracks:
            best = max(result.tracks, key=lambda track: track.confidence)
            seen[best.label].append(best.confidence)
        if msg["status"] == STATUS_OK:
            target = msg["target"]
            target_labels[target["label"]] += 1
            if locks and locks[-1][0] == target["track_id"] and locks[-1][4] == frames - 1:
                renamed += locks[-1][1] != target["label"]
                locks[-1][1], locks[-1][3], locks[-1][4] = target["label"], t, frames
            else:
                locks.append([target["track_id"], target["label"], t, t, frames])
        if msg["summary"]:
            last_summary = msg["summary"]
            said.append((t, last_summary))
        if frames - 1 in wanted:
            canvas = overlay.render(image, cfg, status=msg["status"], phase=msg["phase"], tracks=result.tracks,
                                    target=result.target, guidance=result.guidance, coasting=result.coasting,
                                    summary=last_summary)
            tile = cv2.resize(canvas, (round(canvas.shape[1] * TILE_HEIGHT / canvas.shape[0]), TILE_HEIGHT))
            cv2.putText(tile, f"{t:.1f}s", (tile.shape[1] - 70, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            tiles.append(tile)
    cap.release()
    if not frames:
        print(f"{path.name}: could not read any frame\n")
        return None

    durations = [last - first + 1 / fps for _, _, first, last, _ in locks]
    print(f"{path.name}   {frames} frames, {frames / fps:.1f} s, {width}x{height}")
    print(f"  status     {shares(statuses, frames)}")
    print(f"  target     {shares(target_labels, statuses[STATUS_OK])}")
    if locks:
        print(f"  locks      {len(locks)}, median {statistics.median(durations):.1f} s, longest {max(durations):.1f} s"
              + (f", target renamed {renamed}x while locked" if renamed else ""))
    best_line = "   ".join(f"{label} in {100 * len(conf) / frames:.0f}% (median conf {statistics.median(conf):.2f})"
                           for label, conf in sorted(seen.items(), key=lambda item: -len(item[1])))
    print(f"  strongest  {best_line or 'nothing detected'}")
    print("  said       " + ("\n             ".join(f"{t:5.1f} s  {text}" for t, text in said) or "-"))
    print(f"  detect     {statistics.mean(detect_ms):.1f} ms mean\n")

    if sheet_dir and tiles:
        rows = [np.hstack(tiles[i:i + SHEET_COLUMNS]) for i in range(0, len(tiles), SHEET_COLUMNS)]
        widest = max(row.shape[1] for row in rows)
        rows = [cv2.copyMakeBorder(row, 0, 0, 0, widest - row.shape[1], cv2.BORDER_CONSTANT) for row in rows]
        sheet_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(sheet_dir / f"{path.stem}.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"frames": frames, "statuses": statuses, "locks": len(locks), "said": len(said), "renamed": renamed,
            "seconds": frames / fps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="video files, or folders of them")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--sheets", metavar="DIR", help="save a sheet of annotated stills per clip here")
    parser.add_argument("--target-label", help="only target this label")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    clips = clips_in(args.paths)
    if not clips:
        print("no video files found")
        return 1
    cfg = load_config(args.config)
    detector = create_detector(cfg.detector)
    if args.target_label:
        cfg = dataclasses.replace(cfg, selection=dataclasses.replace(cfg.selection, preferred_label=args.target_label))
    totals, frames, seconds, locks, said, renamed = Counter(), 0, 0.0, 0, 0, 0
    for path in clips:
        report = evaluate(path, cfg, detector, Path(args.sheets) if args.sheets else None)
        if report:
            totals += report["statuses"]
            frames += report["frames"]
            seconds += report["seconds"]
            locks += report["locks"]
            said += report["said"]
            renamed += report["renamed"]
    if frames:
        print(f"ALL {len(clips)} clips   {frames} frames, {seconds:.0f} s")
        print(f"  status     {shares(totals, frames)}")
        print(f"  locks      {locks} ({60 * locks / seconds:.1f} per minute)   renamed while locked {renamed}x   "
              f"sentences {said} ({60 * said / seconds:.1f} per minute)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
