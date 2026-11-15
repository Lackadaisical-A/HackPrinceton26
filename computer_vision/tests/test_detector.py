import dataclasses
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from paul_vision.bake import bake_key, detection_arch, embeddings_path, export_path
from paul_vision.config import DetectorConfig, WeightsConfig
from paul_vision.detector import (CompositeDetector, PreparedClasses, Profile, UltralyticsDetector,
                                  check_class_names, resolve_profile)
from paul_vision.labels import ModelViews
from paul_vision.types import Detection

CFG = DetectorConfig(backend="auto", device="auto", weights=WeightsConfig(torch="big.pt", openvino="small.pt"),
                     classes=["cup", "pill bottle"], imgsz=[384, 640], conf_min=0.15, iou=0.5, max_det=20,
                     agnostic_nms=True, models_dir="models")
INTEL_LAPTOP = (False, ["CPU", "GPU"])
NVIDIA_DESKTOP = (True, ["CPU"])
NVIDIA_LAPTOP = (True, ["CPU", "GPU.0", "GPU.1"])
PLAIN_CPU = (False, ["CPU"])


def profile(hardware, **overrides):
    p = resolve_profile(dataclasses.replace(CFG, **overrides), *hardware)
    return p.backend, p.device, p.weights


def test_auto_picks_cuda_when_there_is_an_nvidia_gpu():
    assert profile(NVIDIA_DESKTOP) == ("torch", "0", "big.pt")
    assert profile(NVIDIA_LAPTOP) == ("torch", "0", "big.pt")  # even next to an Intel GPU


def test_auto_picks_the_intel_gpu_otherwise():
    assert profile(INTEL_LAPTOP) == ("openvino", "xpu", "small.pt")
    assert profile((False, ["CPU", "GPU.0"])) == ("openvino", "xpu", "small.pt")


def test_auto_falls_back_to_openvino_on_cpu():
    assert profile(PLAIN_CPU) == ("openvino", "cpu", "small.pt")


@pytest.mark.parametrize("hardware, overrides, expected", [
    (INTEL_LAPTOP, dict(device="cpu"), ("openvino", "cpu", "small.pt")),
    (INTEL_LAPTOP, dict(device="gpu"), ("openvino", "xpu", "small.pt")),
    (INTEL_LAPTOP, dict(backend="openvino", device="XPU"), ("openvino", "xpu", "small.pt")),
    (INTEL_LAPTOP, dict(backend="torch"), ("torch", "cpu", "big.pt")),
    (NVIDIA_LAPTOP, dict(backend="openvino"), ("openvino", "xpu", "small.pt")),
    (NVIDIA_DESKTOP, dict(backend="openvino"), ("openvino", "cpu", "small.pt")),
    (NVIDIA_DESKTOP, dict(device=0), ("torch", "0", "big.pt")),
    (NVIDIA_DESKTOP, dict(device="cuda"), ("torch", "0", "big.pt")),
    (NVIDIA_DESKTOP, dict(device="cuda:1"), ("torch", "cuda:1", "big.pt")),
    (NVIDIA_DESKTOP, dict(device="cpu"), ("torch", "cpu", "big.pt")),
])
def test_explicit_choices(hardware, overrides, expected):
    assert profile(hardware, **overrides) == expected


@pytest.mark.parametrize("hardware, overrides, message", [
    (INTEL_LAPTOP, dict(backend="torch", device=0), "needs CUDA"),
    (INTEL_LAPTOP, dict(device=0), "openvino backend"),        # auto resolved to openvino; 0 is a CUDA index
    (NVIDIA_DESKTOP, dict(device="xpu"), "torch backend"),
    (NVIDIA_DESKTOP, dict(backend="openvino", device="xpu"), "needs an Intel GPU"),
    (PLAIN_CPU, dict(device="npu"), "openvino backend"),
    ((False, []), dict(), "no devices"),
])
def test_choices_that_cannot_work_are_refused(hardware, overrides, message):
    with pytest.raises(ValueError, match=message):
        profile(hardware, **overrides)


def test_bake_key_changes_with_everything_the_artifact_depends_on():
    base = bake_key("small.pt", ["cup", "box"], [384, 640])
    assert base == bake_key("models/small.pt", ["cup", "box"], (384, 640))  # stable; the folder is irrelevant
    assert len({base,
                bake_key("big.pt", ["cup", "box"], [384, 640]),
                bake_key("small.pt", ["box", "cup"], [384, 640]),   # order sets the class indices
                bake_key("small.pt", ["cup", "box", "bottle"], [384, 640]),
                bake_key("small.pt", ["cup", "box"], [640, 640]),
                bake_key("small.pt", ["cup", "box"])}) == 6


def test_artifact_paths():
    npz, export = embeddings_path(CFG, "small.pt"), export_path(CFG, "small.pt")
    assert npz.parent == export.parent == Path("models")
    assert npz.name.startswith("small-") and npz.suffix == ".npz"
    assert export.name.startswith("small-") and export.name.endswith("_openvino_model")
    assert embeddings_path(CFG, "small.pt") == embeddings_path(dataclasses.replace(CFG, imgsz=[640, 640]), "small.pt")
    assert export_path(CFG, "small.pt") != export_path(dataclasses.replace(CFG, imgsz=[640, 640]), "small.pt")
    assert export_path(CFG, "small.pt") != export_path(dataclasses.replace(CFG, classes=["cup"]), "small.pt")


def test_the_mask_branch_is_dropped_from_segmentation_checkpoints():
    assert detection_arch("yoloe-26n-seg.pt") == "yoloe-26n.yaml"
    assert detection_arch("models/yoloe-26s-seg.pt") == "yoloe-26s.yaml"
    assert detection_arch("custom.pt") is None  # already boxes only: loaded as it is
    # The export is named after the network that is really in it, the embeddings after the checkpoint.
    assert export_path(CFG, "yoloe-26n-seg.pt").name.startswith("yoloe-26n-")
    assert "seg" not in export_path(CFG, "yoloe-26n-seg.pt").name
    assert embeddings_path(CFG, "yoloe-26n-seg.pt").name.startswith("yoloe-26n-seg-")


# --- several detectors as one (YOLOE plus the station markers) ---

class Fixed:
    """A detector that returns the same detections, and remembers the images it was given."""

    def __init__(self, description: str, detections: list[Detection]):
        self.description, self.detections, self.seen = description, detections, []

    def detect(self, image):
        self.seen.append(image)
        return list(self.detections)


def test_a_composite_joins_the_detections_of_every_detector_and_keeps_their_source():
    cup = Detection("cup", 0.9, (0.1, 0.1, 0.3, 0.3), "yolo")
    drop = Detection("drop station", 1.0, (0.5, 0.5, 0.7, 0.7), "aruco")
    objects, stations = Fixed("yoloe", [cup]), Fixed("aruco", [drop])
    composite = CompositeDetector({"objects": objects, "stations": stations})
    image = np.zeros((4, 4, 3), np.uint8)
    assert composite.detect(image) == [cup, drop]
    assert objects.seen == [image] and stations.seen == [image]  # the same image, not a copy per detector


def test_a_composite_times_each_detector_by_name():
    composite = CompositeDetector({"objects": Fixed("yoloe", []), "stations": Fixed("aruco", [])})
    assert composite.timings == {"objects": 0.0, "stations": 0.0}
    composite.detect(np.zeros((4, 4, 3), np.uint8))
    assert set(composite.timings) == {"objects", "stations"}
    assert all(v >= 0.0 for v in composite.timings.values())


def test_a_composite_describes_every_detector():
    composite = CompositeDetector({"objects": Fixed("yoloe on GPU", []), "stations": Fixed("ArUco 4x4", [])})
    assert composite.description == "objects: yoloe on GPU; stations: ArUco 4x4"


def test_a_composite_needs_a_detector():
    with pytest.raises(ValueError, match="at least one detector"):
        CompositeDetector({})


# --- the label library's views in the model (M4) ---

VIEWS = ModelViews(("my flask#0", "my flask#1"), ("my flask", "my flask"), np.zeros((2, 512), np.float32))


def test_the_model_must_detect_the_classes_then_the_views():
    check_class_names(["cup", "pill bottle"], CFG.classes, None)
    check_class_names(["cup", "pill bottle", "my flask#0", "my flask#1"], CFG.classes, VIEWS)
    with pytest.raises(RuntimeError, match=r"paul_vision\.bake --force"):
        check_class_names(["cup", "bottle"], CFG.classes, None)
    with pytest.raises(RuntimeError, match=r"paul_vision\.bake --force"):  # an empty library is the plain model
        check_class_names(["cup", "pill bottle", "my flask#0"], CFG.classes, ModelViews((), (), np.zeros((0, 512))))
    with pytest.raises(RuntimeError, match=r"followed by the label library's views.*paul_vision\.labels rebuild"):
        check_class_names(["cup", "pill bottle", "my flask#1", "my flask#0"], CFG.classes, VIEWS)  # order counts
    with pytest.raises(RuntimeError, match="paul_vision.labels rebuild"):
        check_class_names(["cup", "pill bottle"], CFG.classes, VIEWS)


class _Head:
    """Stands in for a YOLOE network: set_classes records what it was given."""

    def __init__(self, names):
        self.names = dict(enumerate(names))
        self.pe = None

    def set_classes(self, names, pe):
        self.names, self.pe = dict(enumerate(names)), pe


class _Boxes:
    def __init__(self, rows):
        self.xyxyn = np.array([r[:4] for r in rows], np.float32)
        self.conf = np.array([r[4] for r in rows], np.float32)
        self.cls = np.array([r[5] for r in rows], np.float32)

    def __len__(self):
        return len(self.conf)


class _Model:
    """Stands in for ultralytics.YOLOE with a predictor that holds its own copy of the network."""

    def __init__(self, names, rows=()):
        self.model = _Head(names)
        self.predictor = SimpleNamespace(model=SimpleNamespace(backend=SimpleNamespace(model=_Head(names),
                                                                                       names=None)))
        self.rows = list(rows)

    def predict(self, image, **kwargs):
        return [SimpleNamespace(boxes=_Boxes(self.rows))]


def bare_detector(model, backend="torch") -> UltralyticsDetector:
    """An UltralyticsDetector around a stand-in model, without loading weights."""
    d = object.__new__(UltralyticsDetector)
    d._cfg, d.profile, d._args, d._model = CFG, Profile(backend, "cpu", "big.pt"), {}, model
    d._labels = list(CFG.classes)
    d._describe()
    return d


def test_torch_adopt_changes_the_serving_copy_too_and_reports_the_tag_name():
    model = _Model(CFG.classes, rows=[(0.1, 0.1, 0.3, 0.4, 0.8, 3), (0.5, 0.5, 0.6, 0.6, 0.7, 0)])
    d = bare_detector(model)
    pe = np.ones((1, 4, 512), np.float32)
    assert d.adopt(PreparedClasses(("cup", "pill bottle", "my flask#0", "my flask#1"),
                                   ("cup", "pill bottle", "my flask", "my flask"), pe=pe)) is None
    backend = model.predictor.model.backend
    for head in (model.model, backend.model):
        assert list(head.names.values()) == ["cup", "pill bottle", "my flask#0", "my flask#1"] and head.pe is pe
    assert backend.names == backend.model.names
    assert [x.label for x in d.detect(np.zeros((8, 8, 3), np.uint8))] == ["my flask", "cup"]  # never "my flask#1"
    assert d.labels == ["cup", "pill bottle", "my flask", "my flask"] and "tags ['my flask']" in d.description


def test_openvino_adopt_swaps_the_model_and_hands_back_the_old_one():
    old, new = _Model(CFG.classes), _Model([*CFG.classes, "my flask#0"], rows=[(0.1, 0.1, 0.3, 0.4, 0.8, 2)])
    d = bare_detector(old, backend="openvino")
    retired = d.adopt(PreparedClasses(("cup", "pill bottle", "my flask#0"), ("cup", "pill bottle", "my flask"),
                                      model=new, export="x_openvino_model"))
    assert retired is old and d._model is new
    assert [x.label for x in d.detect(np.zeros((8, 8, 3), np.uint8))] == ["my flask"]
