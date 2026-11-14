"""Source switches use real worker threads; no physical cameras are opened."""
import threading

import numpy as np
import pytest

from paul_vision.camera_control import CameraControl
from paul_vision.types import Frame

CAMERAS = [{"id": "usb", "index": 0, "name": "Robot USB"},
           {"id": "integrated", "index": 1, "name": "Integrated Camera"}]


class Source:
    description = "test camera"
    ended = False

    def __init__(self, value=0, ready=None):
        self.closed = False
        self.frame = Frame(np.full((8, 8, 3), value, np.uint8), 10.0, 1)
        self.ready = ready

    def read(self, timeout):
        if self.ready is not None and not self.ready.wait(timeout):
            return None
        return self.frame

    def close(self):
        self.closed = True


def control(cfg, old, factory, **kwargs):
    return CameraControl(old, 0, cfg, discover=lambda: CAMERAS, factory=factory, **kwargs)


def test_old_feed_survives_until_new_frame_then_source_commits(shipped_cfg):
    ready = threading.Event()
    old, new = Source(), Source(200, ready)
    camera = control(shipped_cfg, old, lambda _: new)
    try:
        camera.select("integrated")
        assert camera.status()["pending_id"] == "integrated"
        assert camera.read(0) is old.frame
        assert not old.closed
        with pytest.raises(RuntimeError, match="already in progress"):
            camera.select("usb")
        ready.set()
        camera._worker.join(2)
        assert camera.status()["active_id"] == "usb"
        assert camera.read(0) is new.frame
        assert old.closed and not new.closed
        assert camera.status()["active_id"] == "integrated"
        assert camera.generation == 1
        assert camera.status()["pending_id"] is None
    finally:
        camera.close()
    assert new.closed


@pytest.mark.parametrize("failure", ["open", "frame"])
def test_failed_switch_keeps_old_source_and_releases_candidate(shipped_cfg, failure):
    old, new = Source(), Source()
    new.frame = None
    def factory(_):
        if failure == "open":
            raise RuntimeError("busy")
        return new
    camera = control(shipped_cfg, old, factory)
    try:
        camera.select("integrated")
        camera._worker.join(2)
        assert camera.read(0) is old.frame
        assert camera.status()["active_id"] == "usb"
        assert camera.status()["pending_id"] is None
        assert "Kept the previous source" in camera.status()["error"]
        assert not old.closed
        if failure == "frame":
            assert new.closed
    finally:
        camera.close()


def test_refresh_and_selection_resolve_stable_device_id_to_new_index(shipped_cfg):
    cameras = [dict(item) for item in CAMERAS]
    opened = []
    camera = CameraControl(Source(), 0, shipped_cfg, discover=lambda: cameras,
                           factory=lambda index: (opened.append(index), Source())[1])
    try:
        cameras[1]["index"] = 4
        camera.select("integrated")
        camera._worker.join(2)
        camera.read(0)
        assert opened == [4]
        cameras.pop()
        camera.refresh()
        assert len(camera.status()["sources"]) == 1
        with pytest.raises(ValueError, match="no longer connected"):
            camera.select("integrated")
    finally:
        camera.close()


def test_recording_blocks_switch_and_close_releases_prepared_source(shipped_cfg):
    camera = control(shipped_cfg, Source(), lambda _: pytest.fail("must not open"), allow_switch=False)
    assert not camera.status()["can_switch"]
    with pytest.raises(RuntimeError, match="recording"):
        camera.select("integrated")
    camera.close()
    old, new = Source(), Source()
    camera = control(shipped_cfg, old, lambda _: new)
    camera.select("integrated")
    camera._worker.join(2)
    camera.close()
    assert old.closed and new.closed


def test_discovery_failure_is_recoverable(shipped_cfg):
    def discover():
        raise OSError("disconnected")
    camera = CameraControl(Source(), 0, shipped_cfg, discover=discover)
    assert camera.status()["error"]
    camera._discover = lambda: CAMERAS
    camera.refresh()
    assert camera.status()["error"] is None
    camera.close()
