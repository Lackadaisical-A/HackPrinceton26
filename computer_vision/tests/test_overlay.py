"""The overlay is drawn without a window here, so this runs on machines with no display."""
import dataclasses

import numpy as np
import pytest

from paul_vision.config import OverlayConfig
from paul_vision.overlay import QUIT, PAUSE, SNAPSHOT, OverlayWindow, grip_line, render
from paul_vision.pressure import Contact
from paul_vision.types import STATUSES, Guidance, Track

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


def test_the_crosshair_is_drawn_at_the_aim_point(shipped_cfg, image):
    def marked(canvas, x: float, y: float) -> bool:
        h, w = canvas.shape[:2]
        return np.any(canvas[int(y * h) - 2:int(y * h) + 3, int(x * w) - 2:int(x * w) + 3] != 90)

    centred = render(image, shipped_cfg, status="no_target", phase="search")
    moved = render(image, shipped_cfg, status="no_target", phase="search", aim=(0.7, 0.75))
    assert marked(centred, 0.5, 0.5) and not marked(centred, 0.7, 0.75)
    assert marked(moved, 0.7, 0.75) and not marked(moved, 0.5, 0.5)
    assert np.array_equal(centred, render(image, shipped_cfg, status="no_target", phase="search", aim=(0.5, 0.5)))


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


# --- M2: grip, alert, stations ---

@pytest.mark.parametrize("contact, text", [
    (None, "grip: no pressure data"),
    (Contact(None, None, 0.0), "grip: no pressure data"),       # stale
    (Contact(None, 0.2, 0.0), "grip: deciding 0.20"),
    (Contact(True, 0.71, 0.0), "grip: holding 0.71"),
    (Contact(False, 0.03, 0.0), "grip: open 0.03"),
])
def test_grip_line_says_what_the_pads_say(contact, text):
    assert grip_line(contact)[0] == text


def test_grip_line_names_the_gripper_load_when_that_is_the_source():
    assert grip_line(None, "load")[0] == "grip (load): no load data"
    assert grip_line(Contact(True, 0.42, 0.0), "load")[0] == "grip (load): holding 0.42"
    assert grip_line(Contact(True, 0.42, 0.0), "pads")[0] == "grip: holding 0.42"


def test_the_grip_line_and_the_alert_are_drawn(shipped_cfg, image):
    plain = render(image, shipped_cfg, status="no_target", phase="carry")
    grip = render(image, shipped_cfg, status="no_target", phase="carry", pressure_feed=True,
                  contact=Contact(True, 0.7, 0.0))
    alert = render(image, shipped_cfg, status="no_target", phase="search", pressure_feed=True,
                   contact=Contact(False, 0.02, 0.0), alert="dropped")
    assert np.count_nonzero(grip != plain) > 100
    assert np.count_nonzero(alert != grip) > 1000


def test_without_a_pressure_feed_the_overlay_is_as_in_milestone_1(shipped_cfg, image):
    # Nothing about the grip is drawn when no feed is configured, so eval_clips sheets stay as they were.
    assert np.array_equal(render(image, shipped_cfg, status="no_target", phase="search"),
                          render(image, shipped_cfg, status="no_target", phase="search", contact=None))


def test_a_station_marker_is_drawn_unlike_an_object(shipped_cfg, image):
    as_object = track(5, "drop station", 0.97, cx=0.7, cy=0.4)
    as_marker = Track(5, "drop station", 0.97, as_object.bbox, "aruco")
    drawn_object = render(image, shipped_cfg, status="no_target", phase="search", tracks=[as_object])
    drawn_marker = render(image, shipped_cfg, status="no_target", phase="search", tracks=[as_marker])
    assert np.count_nonzero(drawn_object != drawn_marker) > 100
