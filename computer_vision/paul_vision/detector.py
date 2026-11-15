"""Object detection behind one small interface: an image in, labelled boxes out.

Two runtime profiles, chosen at startup:
  torch     YOLOE in PyTorch on an NVIDIA GPU (the demo machine).
  openvino  A static OpenVINO export on the Intel GPU, or the CPU (the development laptop).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import cv2
import numpy as np

from .config import DetectorConfig
from .geometry import clamp
from .labels import ModelViews
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


def check_class_names(names: Sequence[str], classes: Sequence[str], views: ModelViews | None) -> None:
    """The loaded model must detect detector.classes followed by the label library's views, in that order."""
    expected = list(classes) + (list(views.classes) if views is not None else [])
    if list(names) == expected:
        return
    if views is None or not views.classes:
        raise RuntimeError(f"the loaded model detects {list(names)}, not the configured {list(classes)}; "
                           "rebuild it with `uv run python -m paul_vision.bake --force`")
    raise RuntimeError(f"the loaded model detects {list(names)}, not detector.classes followed by the label "
                       f"library's views {expected}; rebuild the library with "
                       "`uv run python -m paul_vision.labels rebuild`")


@dataclass(frozen=True, eq=False)
class PreparedClasses:
    """A new class list made ready off the frame thread (UltralyticsDetector.prepare), applied by adopt()."""
    names: tuple[str, ...]         # model class names
    labels: tuple[str, ...]        # the label each is published as
    pe: object | None = None       # torch: (1, classes, 512) prompt embeddings
    model: object | None = None    # openvino: the new model, compiled and warmed up
    export: str | None = None


class UltralyticsDetector:
    """YOLOE through Ultralytics, restricted to the configured classes plus the label library's views (M4).

    Each view is its own class (`my flask#0`, `my flask#1`, ...) and is reported under its tag's name. The class
    list changes while frames run in two steps: `prepare` (any thread; the slow part on OpenVINO) and `adopt` (the
    frame thread, between two frames; cheap).
    """

    def __init__(self, cfg: DetectorConfig, profile: Profile, warmup_shape: tuple[int, int] = (720, 1280),
                 views: ModelViews | None = None):
        from . import bake

        self._cfg = cfg
        self.profile = profile
        self._warmup_shape = warmup_shape
        self._args = dict(device=profile.device, imgsz=list(cfg.imgsz), conf=cfg.conf_min, iou=cfg.iou,
                          max_det=cfg.max_det, agnostic_nms=cfg.agnostic_nms, verbose=False)
        if profile.backend == "torch" and profile.device != "cpu":
            self._args["quantize"] = 16  # half precision on the NVIDIA GPU (this replaced Ultralytics' `half`)
        has_views = views is not None and bool(views.classes)
        if profile.backend == "torch":
            self._model = bake.load_promptable(cfg, profile.weights)
            # The configured classes' embeddings, kept to build every later class list from.
            self._base_pe = self._model.model.pe[:, :len(cfg.classes)].detach().float().cpu().clone()
            if has_views:
                prepared = self.prepare(views)
                self._model.set_classes(list(prepared.names), prepared.pe)  # no predictor yet, so this is cheap
        else:
            export_dir = Path(views.export) if has_views else bake.bake_openvino(cfg, profile.weights)
            self._model = self._load_export(export_dir)
        self._labels = list(cfg.classes) + (list(views.labels) if has_views else [])
        self._describe()
        # The first calls compile the network and allocate buffers; pay that before the first real frame.
        blank = np.zeros((*warmup_shape, 3), np.uint8)
        for _ in range(3):
            self.detect(blank)
        # Ultralytics switches OpenCV to a single thread when it is imported, for its training data loaders.
        # Nothing here uses those, and one thread makes the overlay's resize and the recorder several times
        # slower (measured: resize 1.7 -> 12.8 ms, video write 7 -> 19 ms), so put OpenCV's default back.
        cv2.setNumThreads(-1)
        check_class_names([str(n) for n in self._model.names.values()], cfg.classes, views if has_views else None)

    @staticmethod
    def _load_export(export_dir: Path):
        import yaml
        from ultralytics import YOLO

        # An exported model does not know its own task; the export wrote it down next to the network.
        metadata = yaml.safe_load((Path(export_dir) / "metadata.yaml").read_text(encoding="utf-8"))
        return YOLO(str(export_dir), task=metadata.get("task", "segment"))

    def _describe(self) -> None:
        tags = list(dict.fromkeys(label for label in self._labels[len(self._cfg.classes):]))
        self.description = (f"{self.profile.label}, input {self._cfg.imgsz[0]}x{self._cfg.imgsz[1]}, "
                            f"classes {list(self._cfg.classes)}" + (f", tags {tags}" if tags else ""))

    @property
    def labels(self) -> list[str]:
        """The label of each model class, in class order."""
        return list(self._labels)

    def prepare(self, views: ModelViews | None) -> PreparedClasses:
        """Make the class list detector.classes + `views` ready to adopt. Any thread: it never touches the model
        that serves frames. On OpenVINO this loads and compiles `views.export` (seconds) and runs it twice."""
        names = tuple(self._cfg.classes) + (tuple(views.classes) if views is not None else ())
        labels = tuple(self._cfg.classes) + (tuple(views.labels) if views is not None else ())
        if self.profile.backend == "torch":
            import torch

            parts = [self._base_pe]
            if views is not None and views.classes:
                parts.append(torch.from_numpy(np.ascontiguousarray(views.embeddings, np.float32))[None])
            return PreparedClasses(names, labels, pe=torch.cat(parts, dim=1))
        from . import bake

        # Without views, the plain export bake.py made (the label worker makes sure it exists).
        export = Path(views.export) if views is not None and views.export else bake.export_path(self._cfg,
                                                                                                self.profile.weights)
        model = self._load_export(export)
        blank = np.zeros((*self._warmup_shape, 3), np.uint8)
        for _ in range(2):  # the first compiles for the device
            model.predict(blank, **self._args)
        check_class_names([str(n) for n in model.names.values()], self._cfg.classes, views)
        return PreparedClasses(names, labels, model=model, export=str(export))

    def adopt(self, prepared: PreparedClasses) -> object | None:
        """Serve `prepared` from the next frame on. Frame thread only, between two frames. Returns the model it
        replaced (OpenVINO), for the caller to let go of off the frame thread; None on torch."""
        retired = None
        if prepared.model is not None:
            retired, self._model = self._model, prepared.model
        else:
            # Both copies change: model.model, which a predictor rebuild would copy, and the predictor's own deep
            # copy, which serves frames. Calling YOLOE.set_classes instead would drop the predictor, and rebuilding
            # it on the next frame took 382-596 ms. The embeddings stay float32 on the CPU: the head casts them to
            # the input's device and dtype on every call.
            names = list(prepared.names)
            self._model.model.set_classes(names, prepared.pe)
            predictor = self._model.predictor
            if predictor is not None:
                predictor.model.backend.model.set_classes(names, prepared.pe)
                predictor.model.backend.names = predictor.model.backend.model.names
        self._labels = list(prepared.labels)
        self._describe()
        return retired

    def detect(self, image: np.ndarray) -> list[Detection]:
        result = self._model.predict(image, **self._args)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        labels = self._labels
        out = []
        for (x1, y1, x2, y2), conf, cls in zip(boxes.xyxyn.tolist(), boxes.conf.tolist(), boxes.cls.tolist()):
            bbox = (clamp(x1, 0.0, 1.0), clamp(y1, 0.0, 1.0), clamp(x2, 0.0, 1.0), clamp(y2, 0.0, 1.0))
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                out.append(Detection(labels[int(cls)], float(conf), bbox))
        return out


def create_detector(cfg: DetectorConfig, warmup_shape: tuple[int, int] = (720, 1280), *,
                    profile: Profile | None = None, views: ModelViews | None = None) -> UltralyticsDetector:
    profile = profile or resolve_profile(cfg, *probe_hardware())
    banner = f"DETECTOR PROFILE: {profile.label}"
    log.warning("%s\n  %s\n%s", "=" * (len(banner) + 4), banner, "=" * (len(banner) + 4))
    return UltralyticsDetector(cfg, profile, warmup_shape, views)


class CompositeDetector:
    """Several detectors on the same image, one after the other, as one Detector: their detections are joined.

    Used for YOLOE (objects) plus the ArUco StationDetector (stations). Detections keep their own `source`, which
    the tracker and the target selection use to keep the two apart. `timings` holds how long each took on the
    last image, by name, for the metrics line.
    """

    def __init__(self, detectors: dict[str, Detector]):
        if not detectors:
            raise ValueError("CompositeDetector needs at least one detector")
        self.detectors = dict(detectors)
        self.description = "; ".join(f"{name}: {d.description}" for name, d in self.detectors.items())
        self.timings: dict[str, float] = {name: 0.0 for name in self.detectors}

    def detect(self, image: np.ndarray) -> list[Detection]:
        out: list[Detection] = []
        for name, detector in self.detectors.items():
            started = time.perf_counter()
            out.extend(detector.detect(image))
            self.timings[name] = time.perf_counter() - started
        return out
