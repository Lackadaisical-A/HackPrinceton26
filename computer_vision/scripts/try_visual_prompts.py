"""Spike for M4: can a YOLOE visual prompt give one object the user's name, apart from similar objects?

    uv run python scripts/try_visual_prompts.py --ref-frame 2250 --box 294,372,380,515 --name "my flask" \
        --text-name "white thermos" --text-name "white water bottle" --ref2-frame 850 --box2 758,42,848,173 \
        --every 3 --out <dir> --labels <labels.txt> --stills 2250,2190,1560,390,1410

What it does, on the CPU with the torch profile's model (the -seg checkpoint in the detection architecture,
the configured text classes from the baked .npz, input size and thresholds from config.yaml):

1. Takes the box on the reference frame, turns it into a visual prompt embedding and appends it to the four
   text classes under --name. With --ref2-frame, a second embedding of the same object from another
   viewpoint gives three more variants: that one alone, the two averaged, and both kept under one name.
2. Runs every variant over the clip (every --every-th frame) and caches what each one detects in
   <out>/detections.json (delete it, or pass --rerun, after changing any argument).
   Variants: a = text classes only (today); b = text classes + --text-name (needs the CLIP text encoder);
   c = text classes + the visual prompt; c2, avg, both = the second-viewpoint variants.
3. Writes <out>/check/*.jpg: every --check-every-th frame with each distinct box numbered (the union over
   all variants, without names, so the hand check is not steered by any variant). Look at them and write a
   labels file, one line per frame you checked:

       2250: 1=T 3=W 4=W          T = the tagged object, W = water bottle, any other letter = other objects
       1200:                      checked, none of these objects in view
       930: 2=W miss=T@410,300    the tagged object is visible but no variant put a box on it

   The ids refer to <out>/candidates.json, so keep that file with the labels (it is only rewritten with
   --rerun). Frames not listed are not counted.
4. With --labels: for each variant, on the checked frames, how often the tagged object carries the user's
   name and with what confidence, what label it gets otherwise, and how often the name lands on a water
   bottle or anything else. Frames within --near of a reference frame are reported separately because
   they are near-copies of the prompt.
5. --stills: side-by-side pictures of the variants on chosen frames, in <out>/stills/.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paul_vision.bake import detection_arch, embeddings_path, models_dir  # noqa: E402
from paul_vision.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402


# ---------------------------------------------------------------- model

def load_model(cfg, weights: str):
    """Same route as bake.load_promptable: the -seg checkpoint in the detection architecture, baked text classes."""
    from ultralytics import YOLOE

    npz = embeddings_path(cfg, weights)
    if not npz.exists():
        raise SystemExit(f"{npz} is missing; run `uv run python -m paul_vision.bake --backend torch` first")
    arch = detection_arch(weights)
    with models_dir(cfg):
        model = YOLOE(weights) if arch is None else YOLOE(arch).load(weights)
        model.load_prompt_embeddings(npz)
    return model


def visual_pe(model, image: np.ndarray, box, imgsz):
    """One (1, 1, 512) embedding for the object in `box` (pixels of `image`), without touching the model's classes.

    YOLOEVPDetectPredictor letterboxes the image, rasterizes the box into a prompt mask and runs the network up
    to the head's SAVPE module (get_vpe). Unlike predict(refer_image=...), nothing is written to the model.
    """
    from ultralytics.models.yolo.yoloe import YOLOEVPDetectPredictor

    predictor = YOLOEVPDetectPredictor(overrides=dict(task="detect", mode="predict", imgsz=list(imgsz), batch=1,
                                                      device="cpu", verbose=False, save=False))
    predictor.set_prompts({"bboxes": np.asarray([box], np.float32), "cls": np.array([0])})
    predictor.setup_model(model=model.model, verbose=False)
    return predictor.get_vpe(image).detach().float()


def text_pe(cfg, model, text: str):
    with models_dir(cfg):  # the text encoder (mobileclip2_b.ts) lives in models/
        return model.get_text_pe([text]).detach().float()


def with_extra_classes(cfg, weights, extra_names: list[str], extra_pe):
    """A fresh model with the configured text classes followed by `extra_names` (embeddings `extra_pe`)."""
    import torch

    model = load_model(cfg, weights)
    if extra_names:
        base = model.model.pe.detach().float()
        names = [str(n) for n in model.names.values()] + list(extra_names)
        model.set_classes(names, torch.cat([base, extra_pe.to(base.dtype)], dim=1))
    return model


def user_name(label: str) -> str:
    """Model class name -> the name the user gave. Several embeddings of one name are stored as name#1, name#2."""
    return label.split("#", 1)[0]


# ---------------------------------------------------------------- clip

def read_frames(clip: str, wanted: set[int] | None = None):
    cap = cv2.VideoCapture(clip)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {clip}")
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if wanted is None or idx in wanted:
            yield idx, frame
        idx += 1
    cap.release()


def grab(clip: str, frame_no: int) -> np.ndarray:
    for _, frame in read_frames(clip, {frame_no}):
        return frame
    raise SystemExit(f"{clip} has no frame {frame_no}")


def parse_box(text: str):
    box = [float(v) for v in text.split(",")]
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise SystemExit(f"box must be x1,y1,x2,y2 in pixels, got {text!r}")
    return box


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- run

def build_variants(args, cfg, weights):
    """name -> (model, the user name this variant is scored on or None)."""
    import torch

    ref = grab(args.clip, args.ref_frame)
    base = load_model(cfg, weights)
    t0 = time.perf_counter()
    vpe1 = visual_pe(base, ref, parse_box(args.box), cfg.imgsz)
    print(f"visual prompt from frame {args.ref_frame}: {1000 * (time.perf_counter() - t0):.0f} ms")
    variants = {"a": (base, None)}
    for k, text in enumerate(args.text_name):
        variants["b" if k == 0 else f"b{k + 1}"] = (with_extra_classes(cfg, weights, [text], text_pe(cfg, base, text)),
                                                    text)
    variants["c"] = (with_extra_classes(cfg, weights, [args.name], vpe1), args.name)
    if args.ref2_frame is not None:
        vpe2 = visual_pe(base, grab(args.clip, args.ref2_frame), parse_box(args.box2), cfg.imgsz)
        cos = float(torch.nn.functional.cosine_similarity(vpe1[0], vpe2[0]).item())
        print(f"second visual prompt from frame {args.ref2_frame}; cosine to the first: {cos:.3f}")
        mean = torch.nn.functional.normalize(vpe1 + vpe2, dim=-1)
        variants["c2"] = (with_extra_classes(cfg, weights, [args.name], vpe2), args.name)
        variants["avg"] = (with_extra_classes(cfg, weights, [args.name], mean), args.name)
        variants["both"] = (with_extra_classes(cfg, weights, [f"{args.name}#1", f"{args.name}#2"],
                                               torch.cat([vpe1, vpe2], dim=1)), args.name)
    return variants


def run(args, cfg, weights, out: Path) -> dict:
    variants = build_variants(args, cfg, weights)
    pargs = dict(device="cpu", imgsz=list(cfg.imgsz), conf=cfg.conf_min, iou=cfg.iou, max_det=cfg.max_det,
                 agnostic_nms=cfg.agnostic_nms, verbose=False)
    for name, (model, scored) in variants.items():
        print(f"  {name:<5} classes {list(model.names.values())}" + (f"   scored on '{scored}'" if scored else ""))
    dets = {name: {} for name in variants}
    ms = {name: [] for name in variants}
    total = int(cv2.VideoCapture(args.clip).get(cv2.CAP_PROP_FRAME_COUNT))
    wanted = set(range(0, total, args.every)) | {args.ref_frame} | ({args.ref2_frame} - {None})
    t_start = time.perf_counter()
    for n, (idx, frame) in enumerate(read_frames(args.clip, wanted)):
        for name, (model, _) in variants.items():
            t0 = time.perf_counter()
            res = model.predict(frame, **pargs)[0]
            ms[name].append(1000 * (time.perf_counter() - t0))
            b = res.boxes
            dets[name][idx] = [[res.names[int(k)], round(c, 3), *[round(v, 1) for v in xyxy]]
                               for xyxy, c, k in zip(b.xyxy.tolist(), b.conf.tolist(), b.cls.tolist())]
        if n % 100 == 0:
            print(f"  frame {idx}/{total}  {time.perf_counter() - t_start:.0f} s")
    import torch

    data = {"args": {k: v for k, v in vars(args).items() if k not in ("labels", "stills", "rerun")},
            "classes": {name: list(m.names.values()) for name, (m, _) in variants.items()},
            "scored_name": {name: s for name, (_, s) in variants.items()},
            "torch_threads": torch.get_num_threads(),
            "ms_per_frame_median": {name: round(statistics.median(v), 1) for name, v in ms.items()},
            "detections": dets}
    (out / "detections.json").write_text(json.dumps(data), encoding="utf-8")
    return json.loads(json.dumps(data))  # frame keys as strings, as when read back from the cache


# ---------------------------------------------------------------- hand check

def candidates(data: dict, frames) -> dict:
    """Per frame, the distinct boxes over all variants (boxes come from a class-agnostic regression, so the same
    object gets the same box under every variant), numbered left to right."""
    out = {}
    for f in frames:
        boxes = []
        for dets in data["detections"].values():
            for _, _, *box in dets.get(str(f), dets.get(f, [])):
                if all(iou(box, b) < 0.6 for b in boxes):
                    boxes.append(box)
        boxes.sort(key=lambda b: (b[0], b[1]))
        out[str(f)] = {str(i + 1): b for i, b in enumerate(boxes)}
    return out


def write_check_sheets(clip: str, cands: dict, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    frames = sorted(int(f) for f in cands)
    tiles = {}
    for idx, frame in read_frames(clip, set(frames)):
        img = frame.copy()
        for i, (x1, y1, x2, y2) in cands[str(idx)].items():
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 255), 2)
            cv2.putText(img, i, (int(x1) + 2, int(y1) + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(img, i, (int(x1) + 2, int(y1) + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        img = cv2.resize(img, (640, 360))
        cv2.putText(img, f"#{idx}", (540, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        tiles[idx] = img
    for s in range(0, len(frames), 4):
        sheet = np.zeros((720, 1280, 3), np.uint8)
        for j, f in enumerate(frames[s:s + 4]):
            r, c = divmod(j, 2)
            sheet[r * 360:(r + 1) * 360, c * 640:(c + 1) * 640] = tiles[f]
        cv2.imwrite(str(folder / f"check_{frames[s]:04d}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])


def read_labels(path: Path, cands: dict) -> dict:
    """frame -> list of (letter, box); a miss is a small box around the given point."""
    labels = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        frame, _, rest = line.partition(":")
        frame = frame.strip()
        objs = []
        for tok in rest.split():
            key, _, val = tok.partition("=")
            if key == "miss":
                letter, _, pt = val.partition("@")
                x, y = (float(v) for v in pt.split(","))
                objs.append((letter, [x - 15, y - 15, x + 15, y + 15], True))
            else:
                if key not in cands.get(frame, {}):
                    raise SystemExit(f"labels: frame {frame} has no candidate box {key}")
                objs.append((val, cands[frame][key], False))
        labels[int(frame)] = objs
    return labels


def match(box, objs):
    """The labelled object a detection lies on: IoU >= 0.5 with a labelled box, or containing a 'miss' point."""
    best, letter = 0.0, None
    for obj_letter, obj_box, is_point in objs:
        if is_point:
            cx, cy = (obj_box[0] + obj_box[2]) / 2, (obj_box[1] + obj_box[3]) / 2
            if box[0] <= cx <= box[2] and box[1] <= cy <= box[3] and best < 0.5:
                best, letter = 0.5, obj_letter
        else:
            v = iou(box, obj_box)
            if v >= 0.5 and v > best:
                best, letter = v, obj_letter
    return letter


def score(data: dict, labels: dict, args) -> None:
    refs = [f for f in (args.ref_frame, args.ref2_frame) if f is not None]
    groups = {f"away from the reference frames (more than {args.near} frames from {refs})":
              [f for f in labels if all(abs(f - r) > args.near for r in refs)]}
    for k, r in enumerate(refs):
        groups[f"near reference {k + 1} (frame {r} +-{args.near}; near-copies of the prompt for the variants "
               f"built from it)"] = [f for f in labels if abs(f - r) <= args.near]
    print(f"\nhand-checked frames: {len(labels)}; tagged object (T) in view in "
          f"{sum(any(o[0] == 'T' for o in v) for v in labels.values())}; water bottles (W) in "
          f"{sum(any(o[0] == 'W' for o in v) for v in labels.values())} frames "
          f"({sum(o[0] == 'W' for v in labels.values() for o in v)} bottle views)")
    for title, frames in groups.items():
        t_frames = [f for f in frames if any(o[0] == "T" for o in labels[f])]
        w_views = sum(o[0] == "W" for f in frames for o in labels[f])
        print(f"\n== {title}: {len(frames)} checked frames, T in view in {len(t_frames)}, {w_views} water-bottle views")
        print(f"{'variant':<7} {'name scored':<16} {'T named':>9} {'conf med [min-max]':>20} {'T labelled otherwise':<34}"
              f" {'name on W':>10} {'name elsewhere':>15} {'name, T absent':>15}")
        for variant, dets_all in data["detections"].items():
            name = data["scored_name"][variant]
            hits, confs, other_labels = 0, [], Counter()
            on_w = on_other = absent_frames = 0
            for f in frames:
                objs = labels[f]
                dets = dets_all.get(str(f), [])
                t_in_view = any(o[0] == "T" for o in objs)
                named_here = False
                t_best = None
                for label, conf, *box in dets:
                    m = match(box, objs)
                    is_name = name is not None and user_name(label) == name
                    if m == "T":
                        if t_best is None or (is_name and not t_best[2]) or (is_name == t_best[2] and conf > t_best[1]):
                            t_best = (label, conf, is_name)
                    elif is_name:
                        named_here = True
                        if m == "W":
                            on_w += 1
                        else:
                            on_other += 1
                if t_in_view:
                    if t_best and t_best[2]:
                        hits += 1
                        confs.append(t_best[1])
                    else:
                        other_labels[user_name(t_best[0]) if t_best else "none"] += 1
                elif named_here:
                    absent_frames += 1
            conf_txt = (f"{statistics.median(confs):.2f} [{min(confs):.2f}-{max(confs):.2f}]" if confs else "-")
            others = ", ".join(f"{k} {v}" for k, v in other_labels.most_common(4))
            hit_txt = f"{hits}/{len(t_frames)}" if name else "-"
            print(f"{variant:<7} {str(name):<16} {hit_txt:>9} {conf_txt:>20} {others:<34}"
                  f" {on_w if name else '-':>10} {on_other if name else '-':>15} {absent_frames if name else '-':>15}")
    print("\nT named = frames where the box on the tagged object carries the scored name. 'name on W' and "
          "'name elsewhere' count detections (not frames) carrying the name on a water bottle or on anything that "
          "is not the tagged object. 'name, T absent' = frames without the tagged object that still show the name.")


def summary_unlabelled(data: dict) -> None:
    print(f"\nwhole run, no hand check (torch threads {data['torch_threads']}; median ms per frame per variant "
          f"{data['ms_per_frame_median']}):")
    for variant, dets_all in data["detections"].items():
        name = data["scored_name"][variant]
        if not name:
            continue
        confs = [max(c for lab, c, *_ in d if user_name(lab) == name) for d in dets_all.values()
                 if any(user_name(lab) == name for lab, *_ in d)]
        multi = sum(1 for d in dets_all.values() if sum(user_name(lab) == name for lab, *_ in d) > 1)
        print(f"  {variant:<5} '{name}': in {len(confs)}/{len(dets_all)} frames"
              + (f", best conf median {statistics.median(confs):.2f}" if confs else "")
              + f", {multi} frames with the name on more than one box")


# ---------------------------------------------------------------- stills

def stills(args, data: dict, out: Path, frames: list[int]) -> None:
    folder = out / "stills"
    folder.mkdir(parents=True, exist_ok=True)
    shown = [v for v in ("a", "b", "c", "both") if v in data["detections"]]
    for idx, frame in read_frames(args.clip, set(frames)):
        panels = []
        for v in shown:
            img = frame.copy()
            name = data["scored_name"][v]
            title = {"a": "a) text classes only", "b": f"b) text + '{name}' (text prompt)",
                     "c": f"c) text + '{name}' (visual prompt, frame {args.ref_frame})",
                     "both": f"both) text + '{name}' (2 visual prompts)"}[v]
            cv2.rectangle(img, (0, 0), (1280, 44), (0, 0, 0), -1)
            cv2.putText(img, f"{title}   frame {idx}", (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            for label, conf, *box in data["detections"][v].get(str(idx), []):
                is_name = name is not None and user_name(label) == name
                color = (0, 220, 0) if is_name else (0, 165, 255)
                x1, y1, x2, y2 = (int(round(t)) for t in box)
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if is_name else 2)
                text = f"{user_name(label)} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
                ty = y1 if y1 - th - 8 >= 44 else max(y1, 44) + th + 8  # below the title bar, inside the box
                cv2.rectangle(img, (x1, ty - th - 8), (x1 + tw + 4, ty), color, -1)
                cv2.putText(img, text, (x1 + 2, ty - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
            panels.append(cv2.resize(img, (960, 540)))
        while len(panels) % 2:
            panels.append(np.zeros_like(panels[0]))
        rows = [np.hstack(panels[i:i + 2]) for i in range(0, len(panels), 2)]
        path = folder / f"frame_{idx:04d}.jpg"
        cv2.imwrite(str(path), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"still: {path}")


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--clip", default="recordings/run1.mp4")
    p.add_argument("--ref-frame", type=int, required=True, help="frame number the object is tagged on")
    p.add_argument("--box", required=True, help="x1,y1,x2,y2 of the object on that frame, in pixels")
    p.add_argument("--name", required=True, help="the user's name for the object")
    p.add_argument("--text-name", action="append", default=[],
                   help="variant b: the same object named by a text prompt (repeat for more)")
    p.add_argument("--ref2-frame", type=int, help="a second frame showing the object from another viewpoint")
    p.add_argument("--box2", help="the object's box on --ref2-frame")
    p.add_argument("--every", type=int, default=1, help="run on every n-th frame")
    p.add_argument("--check-every", type=int, default=30, help="hand-check sheet every n-th frame")
    p.add_argument("--near", type=int, default=150, help="frames this close to a reference are reported apart")
    p.add_argument("--out", required=True, help="folder for detections, check sheets and stills")
    p.add_argument("--labels", help="hand labels (format above)")
    p.add_argument("--stills", default="", help="comma-separated frame numbers to draw")
    p.add_argument("--rerun", action="store_true", help="run the detector again even if detections.json exists")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = p.parse_args(argv)
    if (args.ref2_frame is None) != (args.box2 is None):
        p.error("--ref2-frame and --box2 go together")
    if args.check_every % args.every:
        p.error("--check-every must be a multiple of --every")

    cfg = load_config(args.config).detector
    weights = cfg.weights.torch
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "detections.json"
    if cache.exists() and not args.rerun:
        data = json.loads(cache.read_text(encoding="utf-8"))
        print(f"using {cache} (pass --rerun after changing arguments); made with {data['args']}")
    else:
        data = run(args, cfg, weights, out)

    cand_path = out / "candidates.json"
    if args.rerun or not cand_path.exists():
        frames = sorted(int(f) for f in data["detections"]["a"] if int(f) % args.check_every == 0)
        cands = candidates(data, frames)
        cand_path.write_text(json.dumps(cands), encoding="utf-8")
        write_check_sheets(args.clip, cands, out / "check")
        print(f"hand-check sheets: {out / 'check'}")
    cands = json.loads(cand_path.read_text(encoding="utf-8"))

    summary_unlabelled(data)
    if args.labels:
        score(data, read_labels(Path(args.labels), cands), args)
    if args.stills:
        stills(args, data, out, [int(f) for f in args.stills.split(",")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
