"""One label change, start to finish, in a child process: python -m paul_vision.label_worker <job.json>

Everything heavy happens here and never in the vision process, because building a YOLOE model or exporting one
inside the vision process stalled its frame loop past the watchdog (measured: 557-816 ms). This process:

  1. loads YOLOE in PyTorch on the CPU (whatever the vision profile is) with detector.classes set,
  2. makes the new view's embedding: from a box on a saved picture at the runtime input size (tag_visual), or from
     a text prompt with the text encoder baked into models/ (tag_text); or none (untag),
  3. writes the views file, the reference picture and the index (library.next.json; library.json when `commit`),
  4. with `export`, builds the OpenVINO export for the new class list in <labels dir>/openvino/.

The vision process starts it at low priority with few torch threads, waits for it without blocking (polling), then
applies the result between two frames. The outcome is written to the job's `result` file as JSON.

Job file (written by label_manager): op (add | remove | rebuild | export), detector (the DetectorConfig as a dict),
weights, labels_dir, max_tags, max_views, threads, export, commit, keep_export, result; for add: name and view
({kind: visual, frame_npy, bbox (pixels)} or {kind: text, prompt}); for remove: name.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from . import labels as lb
from .config import DetectorConfig, WeightsConfig


def detector_config(data: dict) -> DetectorConfig:
    return DetectorConfig(**{**data, "weights": WeightsConfig(**data["weights"])})


def job_detector_dict(cfg: DetectorConfig) -> dict:
    return dataclasses.asdict(cfg)


class _Timer:
    def __init__(self):
        self.parts: dict[str, float] = {}
        self._t = time.perf_counter()

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.parts[name] = round(now - self._t, 3)
        self._t = now


def _limit_threads(threads: int) -> None:
    import torch
    from ultralytics.utils import torch_utils

    # Ultralytics resets torch to min(8, cpus - 1) threads whenever it selects the CPU (each predictor setup and the
    # export); its module-level count is what it resets to.
    torch_utils.NUM_THREADS = threads
    torch.set_num_threads(threads)


def _load_model(det: DetectorConfig, weights: str):
    """YOLOE with detector.classes set (their text embeddings come from the baked npz in models/)."""
    from . import bake

    return bake.load_promptable(det, weights)  # changes into models/ while loading: fine in this process only


def _visual_embedding(model, det: DetectorConfig, frame: np.ndarray, box_px, threads: int) -> np.ndarray:
    """(512,) embedding of the box (pixels of `frame`) at the runtime input size; extraction at another size gave
    cosine 0.87 against the runtime one."""
    from ultralytics.models.yolo.yoloe import YOLOEVPDetectPredictor

    predictor = YOLOEVPDetectPredictor(overrides=dict(task="detect", mode="predict", imgsz=list(det.imgsz), batch=1,
                                                      device="cpu", verbose=False, save=False))
    predictor.setup_model(model=model.model, verbose=False)  # a deep copy of the unfused network
    _limit_threads(threads)
    predictor.set_prompts({"bboxes": np.asarray([box_px], np.float32), "cls": np.array([0])})
    vpe = predictor.get_vpe(frame).detach().float().cpu().numpy()   # (1, 1, 512), L2-normalised
    return vpe.reshape(lb.EMBED_DIM)


class _TextEncoder:
    def __init__(self, model, det: DetectorConfig):
        import torch
        from ultralytics.nn.text_model import MobileCLIPTS

        weight = Path(det.models_dir) / "mobileclip2_b.ts"
        if model.model.text_model != "mobileclip2:b":
            raise RuntimeError(f"this checkpoint uses the text model {model.model.text_model}, not mobileclip2:b")
        if not weight.exists():
            raise RuntimeError(f"{weight} is missing; run `uv run python -m paul_vision.bake --force` once")
        model.model.clip_model = MobileCLIPTS(torch.device("cpu"), weight=str(weight))
        self._model = model

    def __call__(self, text: str) -> np.ndarray:
        pe = self._model.model.get_text_pe([text], cache_clip_model=True).detach().float().cpu().numpy()
        return pe.reshape(lb.EMBED_DIM)


def _save_reference(directory: Path, view_id: str, frame: np.ndarray) -> str:
    import cv2

    rel = f"{lb.REFS_DIR}/{view_id}.png"
    path = directory / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp.png")
    if not cv2.imwrite(str(tmp), frame):
        raise RuntimeError(f"could not write {tmp}")
    os.replace(tmp, path)
    return rel


def _export(model, det: DetectorConfig, weights: str, lib: lb.Library, directory: Path, timer: _Timer) -> Path:
    """The OpenVINO export for detector.classes plus the library's views (reused if it exists)."""
    import torch

    key = lb.export_key(lib, weights, det.imgsz, det.classes)
    out = lb.export_dir(directory, key)
    if lb.export_complete(out):
        return out
    if out.exists():
        shutil.rmtree(out)  # half deleted by prune() while a model still held a file: make it again
    if model is None:
        model = _load_model(det, weights)
        timer.lap("load model")
    base = len(det.classes)
    base_pe = model.model.pe[:, :base].detach().float().cpu()
    views = torch.from_numpy(lb.load_view_embeddings(directory, lib).copy())[None]
    model.set_classes(lb.class_names(det.classes, lib), torch.cat([base_pe, views], dim=1))
    # The exporter writes next to model.pt_path: a bare file name and an empty working folder keep it out of models/.
    model.model.pt_path = Path(weights).name
    staging = directory / lb.EXPORTS_DIR / f".staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    previous = Path.cwd()
    os.chdir(staging)
    try:
        exported = Path(model.export(format="openvino", imgsz=list(det.imgsz), device="cpu", verbose=False)).resolve()
    finally:
        os.chdir(previous)
    shutil.move(str(exported), str(out))
    shutil.rmtree(staging, ignore_errors=True)
    timer.lap("export")
    return out


def run_job(job: dict) -> dict:
    timer = _Timer()
    det = detector_config(job["detector"])
    weights = job["weights"]
    directory = Path(job["labels_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    threads = int(job["threads"])
    lib = lb.load_library(directory)
    lb.prune(directory, [lib], [k for k in [job.get("keep_export")] if k])
    op = job["op"]

    import torch  # noqa: F401  (after the cheap checks above, so a bad job fails fast)
    _limit_threads(threads)
    timer.lap("start")

    model = None
    if op in ("add", "rebuild"):
        if op == "add" and lb.stale_reason(lib, weights, det.imgsz):
            raise RuntimeError(f"the library must be rebuilt first: {lb.stale_reason(lib, weights, det.imgsz)}")
        model = _load_model(det, weights)
        timer.lap("load model")
    if op == "add":
        name, spec = job["name"], job["view"]
        view_id = lb.new_view_id()
        if spec["kind"] == lb.KIND_VISUAL:
            frame = np.load(spec["frame_npy"], allow_pickle=False)
            box = [float(c) for c in spec["bbox"]]
            emb = _visual_embedding(model, det, frame, box, threads)
            timer.lap("embedding")
            image = _save_reference(directory, view_id, frame)
            view = lb.View(view_id, lb.KIND_VISUAL, time.time(), image=image, bbox=tuple(box))
        else:
            emb = _TextEncoder(model, det)(spec["prompt"])
            timer.lap("embedding")
            view = lb.View(view_id, lb.KIND_TEXT, time.time(), prompt=spec["prompt"])
        new = lb.with_view(lib, name, view, max_tags=job["max_tags"], max_views=job["max_views"])
        old = dict(zip([v.id for _, v in lib.views], lb.load_view_embeddings(directory, lib)))
        old[view_id] = emb
        ids = [v.id for _, v in new.views]
        npz = lb.save_view_embeddings(directory, ids, np.stack([old[i] for i in ids]))
        new = dataclasses.replace(new, embeddings=npz, weights=Path(weights).name, imgsz=tuple(det.imgsz))
    elif op == "remove":
        new = lb.subset_embeddings(directory, lib, lb.without_tag(lib, job["name"]))
    elif op == "rebuild":
        text = None
        rows = []
        for _, v in lib.views:
            if v.kind == lb.KIND_VISUAL:
                import cv2

                frame = cv2.imread(str(directory / v.image))
                if frame is None:
                    raise RuntimeError(f"the picture {directory / v.image} is missing or unreadable")
                rows.append(_visual_embedding(model, det, frame, list(v.bbox), threads))
            else:
                text = text or _TextEncoder(model, det)
                rows.append(text(v.prompt))
        timer.lap("embeddings")
        npz = lb.save_view_embeddings(directory, [v.id for _, v in lib.views], np.stack(rows))
        new = dataclasses.replace(lib, embeddings=npz, weights=Path(weights).name, imgsz=tuple(det.imgsz))
    elif op == "export":
        new = lib
    else:
        raise ValueError(f"unknown op {op!r}")

    lb.check_limits(new, max_tags=job["max_tags"], max_views=job["max_views"])
    export = None
    if job["export"]:
        if new.tags:
            export = _export(model, det, weights, new, directory, timer)
        else:
            from . import bake

            export = bake.bake_openvino(det, weights)  # the plain class list: the same export vision starts with
    file = lb.LIBRARY_FILE if job["commit"] else lb.NEXT_FILE
    lb.write_library(directory, new, file)
    timer.lap("write")
    return {"ok": True, "library": file, "export": None if export is None else str(export),
            "classes": lb.class_names(det.classes, new), "timings": timer.parts}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m paul_vision.label_worker <job.json>", file=sys.stderr)
        return 2
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    result_path = Path(job["result"])
    if job.get("low_priority") and hasattr(os, "nice"):
        os.nice(10)  # POSIX; on Windows vision starts this process with BELOW_NORMAL_PRIORITY_CLASS instead
    try:
        result = run_job(job)
        code = 0
    except lb.LabelError as exc:
        result, code = {"ok": False, "error": str(exc), "spoken": True}, 1
    except Exception as exc:  # reported to vision, which says that the change failed
        traceback.print_exc()
        result, code = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 1
    tmp = result_path.with_name(result_path.name + ".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    os.replace(tmp, result_path)
    print("RESULT", json.dumps(result), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
