"""The overlay is drawn without a window here, so this runs on machines with no display."""
import dataclasses

import numpy as np
import pytest

from paul_vision.config import OverlayConfig
from paul_vision.overlay import QUIT, PAUSE, SNAPSHOT, OverlayWindow, render
from paul_vision.types import STATUSES, Guidance

from conftest import track


@pytest.fixture
def image():
    return np.full((720, 1280, 3), 90, np.uint8)


@pytest.mark.parametrize("status", sorted(STATUSES))
def test_render_handles_every_status(shipped_cfg, image, status):
    before = image.copy()
    canvas = render(image, shipped_cfg, status=status, phase="search", fps=31.5, latency_ms=42.0,
                    summary="Target lost.", banner="PAUSED")
    assert canvas.dtype == np.uint8 and canvas.shape[2] == 3
    assert canvas.shape[1] == min(1280, shipped_cfg.overlay.max_width)
    assert canvas.shape[0] * 1280 == pytest.approx(canvas.shape[1] * 720, abs=1280)  # aspect ratio kept
    assert np.array_equal(image, before)  # the frame itself is never drawn on


def test_render_draws_the_target_other_tracks_and_the_arrow(shipped_cfg, image):
    target = track(1, "cup", 0.9, cx=0.25, cy=0.5)
    other = track(2, "box", 0.5, cx=0.8, cy=0.3)
    plain = render(image, shipped_cfg, status="no_target", phase="search")
    for guidance in (Guidance(-0.5, 0.0, "near", False), Guidance(0.0, 0.0, "in_reach", True)):
        for coasting in (False, True):
            drawn = render(image, shipped_cfg, status="ok", phase="approach", tracks=[target, other], target=target,
                           guidance=guidance, coasting=coasting, summary="Cup ahead, left.")
            assert drawn.shape == plain.shape
            assert np.count_nonzero(drawn != plain) > 500


def test_a_small_frame_is_not_upscaled(shipped_cfg):
    cfg = dataclasses.replace(shipped_cfg, overlay=OverlayConfig(enabled=True, max_width=960))
    small = np.zeros((240, 320, 3), np.uint8)
    assert render(small, cfg, status="no_target", phase="search").shape == (240, 320, 3)


def test_keys_map_to_actions():
    window = OverlayWindow()  # never shown, so no display is needed
    assert window._action(ord("q")) == QUIT
    assert window._action(27) == QUIT
    assert window._action(ord("s") | 0x100000) == SNAPSHOT  # modifier bits above the character are ignored
    assert window._action(ord(" ")) == PAUSE
    assert window._action(ord("x")) is None
    assert window._action(-1) is None
