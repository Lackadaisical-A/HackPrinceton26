"""Shared helpers. Logic tests build their own config objects, so tuning config.yaml never breaks them."""
import pytest

from paul_vision.config import load_config
from paul_vision.types import Detection, Track


@pytest.fixture(scope="session")
def shipped_cfg():
    return load_config()


def box(cx: float, cy: float, w: float = 0.2, h: float = 0.2):
    """Normalized [x1, y1, x2, y2] box from its centre and size."""
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def det(label: str = "cup", conf: float = 0.9, cx: float = 0.5, cy: float = 0.5, w: float = 0.2, h: float = 0.2,
        source: str = "yolo") -> Detection:
    return Detection(label, conf, box(cx, cy, w, h), source)


def track(track_id: int = 1, label: str = "cup", conf: float = 0.9, cx: float = 0.5, cy: float = 0.5,
          w: float = 0.2, h: float = 0.2) -> Track:
    return Track(track_id, label, conf, box(cx, cy, w, h))
