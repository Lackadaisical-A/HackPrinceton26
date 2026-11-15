#!/usr/bin/env python
"""Compatibility names forwarding to the current so101_slider_follower adapter.

The current adapter uses calibrated multi-turn travel, Phase-28 metadata, and
session-reference checks. New code should import so101_slider_follower.
"""

from __future__ import annotations

from dataclasses import dataclass

from so101_slider_follower import (
    ALL_MOTORS,
    ARM_MOTORS,
    SLIDER,
    SLIDER_ENCODER_MAX,
    SLIDER_ENCODER_MIN,
    SO101SliderFollower,
    SO101SliderFollowerConfig,
    calibrate_slider_motor_only,
    load_arm_calibration,
    save_combined_slider_calibration,
    setup_slider_motor_id,
    slider_calibration_from_endpoints,
    slider_calibration_path,
    stock_arm_calibration_candidates,
    stock_arm_calibration_path,
    validate_slider_id,
)

# Deprecated arguments retained for older callers. Both values are ignored.
DEFAULT_SLIDER_RANGE_MIN = SLIDER_ENCODER_MIN
DEFAULT_SLIDER_GOAL_SPEED = 0


@dataclass
class SO101LeSliderPositionConfig(SO101SliderFollowerConfig):
    slider_range_min: int = DEFAULT_SLIDER_RANGE_MIN
    slider_goal_speed: int = DEFAULT_SLIDER_GOAL_SPEED


SO101LeSliderPositionFollower = SO101SliderFollower
leslider_calibration_path = slider_calibration_path
save_combined_leslider_calibration = save_combined_slider_calibration


def validate_slider_settings(slider_id: int, *_retired_values: int) -> None:
    validate_slider_id(slider_id)


__all__ = [
    "ALL_MOTORS",
    "ARM_MOTORS",
    "DEFAULT_SLIDER_GOAL_SPEED",
    "DEFAULT_SLIDER_RANGE_MIN",
    "SLIDER",
    "SLIDER_ENCODER_MAX",
    "SLIDER_ENCODER_MIN",
    "SO101LeSliderPositionConfig",
    "SO101LeSliderPositionFollower",
    "calibrate_slider_motor_only",
    "leslider_calibration_path",
    "load_arm_calibration",
    "save_combined_leslider_calibration",
    "setup_slider_motor_id",
    "slider_calibration_from_endpoints",
    "stock_arm_calibration_candidates",
    "stock_arm_calibration_path",
    "validate_slider_settings",
]
