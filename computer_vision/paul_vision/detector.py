"""Object detection behind one small interface: an image in, labelled boxes out.

Two runtime profiles, chosen at startup:
  torch     YOLOE in PyTorch on an NVIDIA GPU (the demo machine).
  openvino  A static OpenVINO export on the Intel GPU, or the CPU (the development laptop).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

import cv2
import numpy as np

from .config import DetectorConfig
from .geometry import clamp
from .types import Detection

log = logging.getLogger(__name__)


class Detector(Protocol):
    description: str

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Detections in one BGR image, with boxes normalized to the image."""


@dataclass(frozen=True)
class Profile:
    backend: str   # torch | openvino
    device: str    # Ultralytics device name: "0", "cuda:1", "cpu", "xpu" (Intel GPU)
    weights: str

    @property
    def label(self) -> str:
        where = {"cpu": "CPU", "xpu": "Intel GPU"}.get(self.device.partition(":")[0], f"CUDA {self.device}")
        return f"{self.backend} on {where}, {self.weights}"


def probe_hardware() -> tuple[bool, list[str]]:
    """(CUDA usable from torch, OpenVINO device names such as ['CPU', 'GPU'])."""
    import torch

    try:
        import openvino

        devices = list(openvino.Core().available_devices)
    except Exception as exc:  # a broken OpenVINO install must not stop the torch profile
        log.warning("OpenVINO is unavailable: %s", exc)
        devices = []
    return bool(torch.cuda.is_available()), devices


def resolve_profile(cfg: DetectorConfig, cuda_available: bool, openvino_devices: Sequence[str]) -> Profile:
    """Turn `backend: auto` / `device: auto` into a concrete choice, refusing ones that cannot work.

    Refusing matters: Ultralytics would otherwise fall back to a slow device with only a warning.
    """
    backend = cfg.backend
    device = str(cfg.device).strip().lower()
    intel_gpu = any(name.startswith("GPU") for name in openvino_devices)
    if backend == "auto":
        backend = "torch" if cuda_available else "openvino"

    if backend == "torch":
        if device == "auto":
            device = "0" if cuda_available else "cpu"
        elif device == "cuda":
            device = "0"
        is_cuda = device.isdigit() or device.startswith("cuda:")
        if not (is_cuda or device == "cpu"):
            raise ValueError(f"detector.device={cfg.device!r} does not work with the torch backend; "
                             "use auto, cpu or a CUDA index such as 0")
        if is_cuda and not cuda_available:
            raise ValueError(f"detector.device={cfg.device!r} needs CUDA, but torch cannot see an NVIDIA GPU. "
                             "On the demo machine run with `uv run --extra cu130 ...`; on a machine "
                             "without one use backend: auto")
        return Profile("torch", device, cfg.weights.torch)

    if not openvino_devices:
        raise ValueError("the openvino backend was selected but OpenVINO reports no devices")
    if device == "auto":
        device = "xpu" if intel_gpu else "cpu"
    elif device in ("gpu", "igpu"):
        device = "xpu"
    if device.partition(":")[0] not in ("cpu", "xpu"):
        raise ValueError(f"detector.device={cfg.device!r} does not work with the openvino backend; "
                         "use auto, cpu or xpu (the Intel GPU)")
    if device.startswith("xpu") and not intel_gpu:
        raise ValueError(f"detector.device={cfg.device!r} needs an Intel GPU, but OpenVINO only reports "
                         f"{list(openvino_devices)}")
    return Profile("openvino", device, cfg.weights.openvino)


class UltralyticsDetector:
    """YOLOE through Ultralytics, restricted to the configured classes."""

    def __init__(self, cfg: DetectorConfig, profile: Profile, warmup_shape: tuple[int, int] = (720, 1280)):
        from . import bake

        self._cfg = cfg
        self.profile = profile
        if profile.backend == "torch":
            self._model = bake.load_promptable(cfg, profile.weights)
        else:
            import yaml
            from ultralytics import YOLO

            export_dir = bake.bake_openvino(cfg, profile.weights)
            # An exported model does not know its own task; the export wrote it down next to the network.
            metadata = yaml.safe_load((export_dir / "metadata.yaml").read_text(encoding="utf-8"))
            self._model = YOLO(str(export_dir), task=metadata.get("task", "segment"))
        self._args = dict(device=profile.device, imgsz=list(cfg.imgsz), conf=cfg.conf_min, iou=cfg.iou,
                          max_det=cfg.max_det, agnostic_nms=cfg.agnostic_nms, verbose=False)
        if profile.backend == "torch" and profile.device != "cpu":
            self._args["quantize"] = 16  # half precision on the NVIDIA GPU (this replaced Ultralytics' `half`)
        self.description = f"{profile.label}, input {cfg.imgsz[0]}x{cfg.imgsz[1]}, classes {list(cfg.classes)}"
        # The first calls compile the network and allocate buffers; pay that before the first real frame.
        blank = np.zeros((*warmup_shape, 3), np.uint8)
        for _ in range(3):
            self.detect(blank)
        # Ultralytics switches OpenCV to a single thread when it is imported, for its training data loaders.
        # Nothing here uses those, and one thread makes the overlay's resize and the recorder several times
        # slower (measured: resize 1.7 -> 12.8 ms, video write 7 -> 19 ms), so put OpenCV's default back.
        cv2.setNumThreads(-1)
        names = [str(n) for n in self._model.names.values()]
        if names != list(cfg.classes):
            raise RuntimeError(f"the loaded model detects {names}, not the configured {list(cfg.classes)}; "
                               "rebuild it with `uv run python -m paul_vision.bake --force`")

    def detect(self, image: np.ndarray) -> list[Detection]:
        result = self._model.predict(image, **self._args)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        names = result.names
        out = []
        for (x1, y1, x2, y2), conf, cls in zip(boxes.xyxyn.tolist(), boxes.conf.tolist(), boxes.cls.tolist()):
            bbox = (clamp(x1, 0.0, 1.0), clamp(y1, 0.0, 1.0), clamp(x2, 0.0, 1.0), clamp(y2, 0.0, 1.0))
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                out.append(Detection(str(names[int(cls)]), float(conf), bbox))
        return out


def create_detector(cfg: DetectorConfig, warmup_shape: tuple[int, int] = (720, 1280)) -> UltralyticsDetector:
    profile = resolve_profile(cfg, *probe_hardware())
    banner = f"DETECTOR PROFILE: {profile.label}"
    log.warning("%s\n  %s\n%s", "=" * (len(banner) + 4), banner, "=" * (len(banner) + 4))
    return UltralyticsDetector(cfg, profile, warmup_shape)
