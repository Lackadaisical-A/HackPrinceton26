"""One-time preparation of the detector for the configured class list.

    uv run python -m paul_vision.bake            # what this machine needs
    uv run python -m paul_vision.bake --backend all

Turning class names into prompt embeddings needs a CLIP tokenizer and a 250 MB text encoder that
Ultralytics downloads on first use. Baking does that once and stores the result in models/, so a
normal start needs neither the network nor CLIP:

    <weights>-<key>.npz               prompt embeddings (torch profile loads these)
    <network>-<key>_openvino_model/   static OpenVINO export with the classes built in (intel profile)

Released YOLOE checkpoints are segmentation models (`...-seg.pt`). Guidance only uses boxes, and
turning mask coefficients into masks costs about 5 ms per detected object, so the network is always
built without its mask branch (see `load_promptable`). Boxes and confidences are unchanged.

The key is a hash of everything the artifact depends on, so editing detector.classes or
detector.imgsz can never silently reuse a stale artifact; the detector re-bakes when one is missing.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Iterator, Sequence

from .config import DEFAULT_CONFIG_PATH, DetectorConfig, load_config

log = logging.getLogger(__name__)


def bake_key(weights: str, classes: Sequence[str], imgsz: Sequence[int] | None = None) -> str:
    payload = json.dumps({"weights": Path(weights).name, "classes": list(classes),
                          "imgsz": None if imgsz is None else list(imgsz)}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def embeddings_path(cfg: DetectorConfig, weights: str) -> Path:
    return Path(cfg.models_dir) / f"{Path(weights).stem}-{bake_key(weights, cfg.classes)}.npz"


def detection_arch(weights: str) -> str | None:
    """The architecture file for the same network without its mask branch; None if there is no mask branch."""
    stem = Path(weights).stem
    return f"{stem.removesuffix('-seg')}.yaml" if stem.endswith("-seg") else None


def export_path(cfg: DetectorConfig, weights: str) -> Path:
    # Named after the network that is actually exported (no "-seg": the mask branch is gone).
    # Ultralytics recognises an OpenVINO model by the directory suffix, so that has to stay.
    stem = Path(detection_arch(weights) or weights).stem
    return Path(cfg.models_dir) / f"{stem}-{bake_key(weights, cfg.classes, cfg.imgsz)}_openvino_model"


@contextlib.contextmanager
def models_dir(cfg: DetectorConfig) -> Iterator[Path]:
    """Work inside models/: Ultralytics downloads weights and the text encoder into the current directory."""
    path = Path(cfg.models_dir)
    path.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(previous)


def load_promptable(cfg: DetectorConfig, weights: str):
    """The YOLOE network, boxes only, with the configured classes set (baking the embeddings first if needed).

    A `-seg` checkpoint is loaded into the matching detection architecture, which is the route the
    Ultralytics YOLOE docs give for a detection-only model: every weight except the mask branch transfers.
    """
    from ultralytics import YOLOE

    npz = embeddings_path(cfg, weights)
    arch = detection_arch(weights)
    with models_dir(cfg):
        model = YOLOE(weights) if arch is None else YOLOE(arch).load(weights)
        if npz.exists():
            model.load_prompt_embeddings(npz)
        else:
            log.warning("baking prompt embeddings for %s (first time for this class list; this needs the CLIP "
                        "text encoder, which is downloaded once into %s)", list(cfg.classes), cfg.models_dir)
            model.set_classes(list(cfg.classes))
            model.save_prompt_embeddings(npz)
            log.info("saved %s", npz)
    return model


def bake_embeddings(cfg: DetectorConfig, weights: str, force: bool = False) -> Path:
    npz = embeddings_path(cfg, weights)
    if force and npz.exists():
        npz.unlink()
    if not npz.exists():
        load_promptable(cfg, weights)
    return npz


def bake_openvino(cfg: DetectorConfig, weights: str, force: bool = False) -> Path:
    target = export_path(cfg, weights)
    if target.exists() and not force:
        return target
    if force:
        bake_embeddings(cfg, weights, force=True)
    model = load_promptable(cfg, weights)
    log.warning("exporting %s to OpenVINO at %dx%d (h x w); this takes a minute", weights, *cfg.imgsz)
    with models_dir(cfg):
        exported = Path(model.export(format="openvino", imgsz=list(cfg.imgsz), verbose=False)).resolve()
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(exported), str(target))
    log.info("saved %s", target)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.bake", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--backend", choices=["auto", "torch", "openvino", "all"], default="auto",
                        help="auto = whatever this machine would run (default); all = both profiles")
    parser.add_argument("--force", action="store_true", help="rebuild even if the artifacts exist")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    cfg = load_config(args.config).detector
    if args.backend == "all":
        backends = ["torch", "openvino"]
    elif args.backend == "auto":
        from .detector import probe_hardware, resolve_profile
        backends = [resolve_profile(cfg, *probe_hardware()).backend]
    else:
        backends = [args.backend]

    for backend in backends:
        if backend == "torch":
            path = bake_embeddings(cfg, cfg.weights.torch, args.force)
        else:
            path = bake_openvino(cfg, cfg.weights.openvino, args.force)
        print(f"{backend}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
