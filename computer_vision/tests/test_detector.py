import dataclasses
from pathlib import Path

import pytest

from paul_vision.bake import bake_key, detection_arch, embeddings_path, export_path
from paul_vision.config import DetectorConfig, WeightsConfig
from paul_vision.detector import resolve_profile

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
