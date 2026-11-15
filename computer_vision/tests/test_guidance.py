import dataclasses
import math

import pytest
from conftest import box, track

from paul_vision.config import DistanceConfig, DistanceThresholds, GuidanceConfig
from paul_vision.guidance import GuidanceFilter, classify_distance, raw_offset, schmitt_level

GUIDANCE = GuidanceConfig(smoothing_tau_s=0.12, deadzone_enter=0.10, deadzone_exit=0.14, aim=[0.5, 0.5])
NO_SMOOTHING = dataclasses.replace(GUIDANCE, smoothing_tau_s=0.0)
THRESHOLDS = DistanceThresholds(near=0.05, in_reach=0.20)
DISTANCE = DistanceConfig(hysteresis=0.15, thresholds={
    "default": THRESHOLDS,
    "box": DistanceThresholds(near=0.10, in_reach=0.40),
})


def sized(fraction: float, label: str = "cup"):
    """A centred target whose box fills `fraction` of the frame."""
    side = math.sqrt(fraction)
    return track(label=label, w=side, h=side)


# --- offsets ---

@pytest.mark.parametrize("cx, cy, dx, dy", [
    (0.5, 0.5, 0.0, 0.0),
    (0.75, 0.5, 0.5, 0.0),    # right of centre: positive dx
    (0.25, 0.5, -0.5, 0.0),   # left: negative dx
    (0.5, 0.75, 0.0, 0.5),    # below centre: positive dy
    (0.5, 0.25, 0.0, -0.5),   # above: negative dy
    (1.0, 0.0, 1.0, -1.0),
])
def test_raw_offset_sign_and_scale(cx, cy, dx, dy):
    assert raw_offset(box(cx, cy)) == pytest.approx((dx, dy))


def test_raw_offset_is_clamped():
    assert raw_offset((1.2, -0.4, 1.4, -0.2)) == (1.0, -1.0)


@pytest.mark.parametrize("cx, cy, dx, dy", [
    (0.6, 0.7, 0.0, 0.0),     # on the aim point: no offset, wherever it is in the frame
    (0.5, 0.5, -0.2, -0.4),   # the frame centre is up and left of this aim
    (0.85, 0.7, 0.5, 0.0),
    (0.0, 1.0, -1.0, 0.6),    # clamped on x only
])
def test_raw_offset_is_measured_from_the_aim_point(cx, cy, dx, dy):
    assert raw_offset(box(cx, cy), (0.6, 0.7)) == pytest.approx((dx, dy))


def test_the_filter_measures_from_the_configured_aim():
    cfg = dataclasses.replace(NO_SMOOTHING, aim=[0.6, 0.7])
    g = GuidanceFilter(cfg, DISTANCE).update(track(cx=0.6, cy=0.72), t=0.0)
    assert (g.dx, g.dy) == pytest.approx((0.0, 0.04))
    assert g.aligned


def test_set_aim_moves_the_reference_and_restarts_the_smoothing():
    filt = GuidanceFilter(GUIDANCE, DISTANCE)
    filt.update(track(cx=0.7, cy=0.6), t=0.0)
    assert not filt.update(track(cx=0.7, cy=0.6), t=0.03).aligned
    filt.set_aim(0.7, 0.6)
    g = filt.update(track(cx=0.7, cy=0.6), t=0.06)
    assert filt.aim == (0.7, 0.6)
    assert (g.dx, g.dy) == pytest.approx((0.0, 0.0))  # jumped to the new reference, not glided there
    assert g.aligned


# --- smoothing ---

def test_first_sample_is_not_smoothed():
    g = GuidanceFilter(GUIDANCE, DISTANCE).update(track(cx=0.8, cy=0.3), t=0.0)
    assert (g.dx, g.dy) == pytest.approx((0.6, -0.4))


def test_smoothing_follows_the_time_constant():
    filt = GuidanceFilter(GUIDANCE, DISTANCE)
    filt.update(track(cx=0.5), t=0.0)
    g = filt.update(track(cx=1.0), t=0.12)  # one time constant after a step from 0 to 1
    assert g.dx == pytest.approx(1 - math.exp(-1), abs=1e-6)


def test_smoothing_does_not_depend_on_frame_rate():
    def settle(fps: int) -> float:
        filt = GuidanceFilter(GUIDANCE, DISTANCE)
        filt.update(track(cx=0.5), t=0.0)
        for i in range(1, fps // 2 + 1):  # half a second after the step
            g = filt.update(track(cx=1.0), t=i / fps)
        return g.dx

    assert settle(20) == pytest.approx(settle(60), abs=1e-6)


def test_tau_zero_disables_smoothing():
    filt = GuidanceFilter(NO_SMOOTHING, DISTANCE)
    filt.update(track(cx=0.5), t=0.0)
    assert filt.update(track(cx=0.9), t=0.03).dx == pytest.approx(0.8)


def test_reset_jumps_to_the_new_target_instead_of_gliding():
    filt = GuidanceFilter(GUIDANCE, DISTANCE)
    filt.update(track(1, cx=0.1), t=0.0)
    glided = filt.update(track(1, cx=0.9), t=0.03)
    jumped = filt.update(track(2, cx=0.9), t=0.06, reset=True)
    assert glided.dx < 0
    assert jumped.dx == pytest.approx(0.8)


# --- aligned ---

def test_aligned_has_enter_and_exit_thresholds():
    filt = GuidanceFilter(NO_SMOOTHING, DISTANCE)

    def aligned_at(dx: float, t: float) -> bool:
        return filt.update(track(cx=0.5 + dx / 2), t).aligned

    assert not aligned_at(0.12, 0.0)   # inside exit but never entered
    assert aligned_at(0.09, 0.1)       # entered
    assert aligned_at(0.13, 0.2)       # stays aligned until past deadzone_exit
    assert not aligned_at(0.15, 0.3)
    assert not aligned_at(0.12, 0.4)   # must come back inside deadzone_enter


def test_aligned_uses_the_combined_offset():
    filt = GuidanceFilter(NO_SMOOTHING, DISTANCE)
    assert not filt.update(track(cx=0.54, cy=0.54), t=0.0).aligned  # 0.08 on each axis = 0.113 combined
    assert filt.update(track(cx=0.53, cy=0.53), t=0.1).aligned


# --- distance ---

@pytest.mark.parametrize("fraction, hint", [(0.01, "far"), (0.049, "far"), (0.06, "near"),
                                            (0.19, "near"), (0.25, "in_reach"), (0.9, "in_reach")])
def test_classify_distance_without_history(fraction, hint):
    assert classify_distance(fraction, THRESHOLDS, None, 0.15) == hint


def test_classify_distance_hysteresis():
    assert classify_distance(0.055, THRESHOLDS, "far", 0.15) == "far"       # needs > 0.0575
    assert classify_distance(0.058, THRESHOLDS, "far", 0.15) == "near"
    assert classify_distance(0.045, THRESHOLDS, "near", 0.15) == "near"     # needs < 0.0425
    assert classify_distance(0.042, THRESHOLDS, "near", 0.15) == "far"
    assert classify_distance(0.22, THRESHOLDS, "near", 0.15) == "near"      # needs > 0.23
    assert classify_distance(0.18, THRESHOLDS, "in_reach", 0.15) == "in_reach"  # needs < 0.17
    assert classify_distance(0.50, THRESHOLDS, "far", 0.15) == "in_reach"   # can skip a level
    assert classify_distance(0.01, THRESHOLDS, "in_reach", 0.15) == "far"


def test_schmitt_level_never_flickers_on_a_boundary():
    level, changes = None, 0
    for i in range(200):
        value = 0.05 * (1 + 0.1 * (-1) ** i)  # +-10% around the bound, inside the 15% margin
        new = schmitt_level(value, (0.05, 0.20), level, 0.15)
        changes += level is not None and new != level
        level = new
    assert changes == 0


def test_distance_hint_uses_per_label_thresholds():
    assert GuidanceFilter(NO_SMOOTHING, DISTANCE).update(sized(0.07, "cup"), t=0.0).distance_hint == "near"
    assert GuidanceFilter(NO_SMOOTHING, DISTANCE).update(sized(0.07, "box"), t=0.0).distance_hint == "far"
    assert GuidanceFilter(NO_SMOOTHING, DISTANCE).update(sized(0.07, "unlisted"), t=0.0).distance_hint == "near"


def test_distance_hint_progresses_as_the_object_grows():
    filt = GuidanceFilter(NO_SMOOTHING, DISTANCE)
    hints = [filt.update(sized(f), t=i * 0.1).distance_hint for i, f in enumerate([0.02, 0.055, 0.08, 0.21, 0.30])]
    assert hints == ["far", "far", "near", "near", "in_reach"]


def test_reset_clears_aligned_and_distance_history():
    filt = GuidanceFilter(NO_SMOOTHING, DISTANCE)
    filt.update(sized(0.30), t=0.0)
    g = filt.update(sized(0.18), t=0.1, reset=True)  # would stay in_reach with history
    assert g.distance_hint == "near"
