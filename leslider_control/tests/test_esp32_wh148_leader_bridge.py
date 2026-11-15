"""Hardware-free tests for the ESP32 six-pot SO-101 bridge."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "esp32_wh148_leader_bridge.py"


@pytest.fixture(scope="module")
def bridge():
    script_directory = str(SCRIPT_PATH.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location("esp32_wh148_leader_bridge_for_tests", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "line",
    [
        "100,200,300,400,500,600",
        "32=100 33=200 34=300 35=400 25=500 26=600",
        '{"values":{"32":100,"33":200,"34":300,"35":400,"25":500,"26":600}}',
        '{"filtered":[100,200,300,400,500,600]}',
    ],
)
def test_parse_all_supported_six_channel_formats(bridge, line):
    assert bridge.parse_pot_values(line, bridge.DEFAULT_PINS) == {
        "32": 100.0,
        "33": 200.0,
        "34": 300.0,
        "35": 400.0,
        "25": 500.0,
        "26": 600.0,
    }


def test_absolute_calibration_endpoints_map_to_each_robot_endpoint(bridge):
    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]
    calibrations = {
        pin: bridge.PotCalibration(low=100.0, center=2050.0, high=4000.0) for pin in bridge.DEFAULT_PINS
    }
    limits = bridge.dry_run_joint_limits(channels)
    spans = dict.fromkeys(bridge.MOTORS, 30.0)

    minimums = bridge.compute_targets(
        dict.fromkeys(bridge.DEFAULT_PINS, 100.0),
        channels,
        calibrations,
        dict.fromkeys(bridge.DEFAULT_PINS, 2050.0),
        dict.fromkeys(bridge.MOTORS, 0.0),
        limits,
        spans,
        set(),
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )
    maximums = bridge.compute_targets(
        dict.fromkeys(bridge.DEFAULT_PINS, 4000.0),
        channels,
        calibrations,
        dict.fromkeys(bridge.DEFAULT_PINS, 2050.0),
        dict.fromkeys(bridge.MOTORS, 0.0),
        limits,
        spans,
        set(),
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )

    for motor in bridge.MOTORS:
        assert minimums[motor] == pytest.approx(limits[motor][0])
        assert maximums[motor] == pytest.approx(limits[motor][1])


def test_invert_swaps_the_leader_and_robot_endpoints(bridge):
    channel = bridge.Channel(pin="32", motor="shoulder_pan")
    limits = bridge.dry_run_joint_limits([channel])
    targets = bridge.compute_targets(
        {"32": 4095.0},
        [channel],
        {"32": bridge.PotCalibration(low=0.0, center=2047.5, high=4095.0)},
        {"32": 2047.5},
        {"shoulder_pan": 0.0},
        limits,
        {"shoulder_pan": 30.0},
        {"shoulder_pan"},
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )

    assert targets["shoulder_pan"] == pytest.approx(limits["shoulder_pan"][0])


def test_dry_run_limits_and_udp_positions_match_the_urdf_exactly(bridge):
    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]
    limits = bridge.dry_run_joint_limits(channels)
    lower_targets = {motor: limits[motor][0] for motor in bridge.MOTORS}
    upper_targets = {motor: limits[motor][1] for motor in bridge.MOTORS}

    lower_radians = bridge.targets_to_urdf_radians(lower_targets, limits)
    upper_radians = bridge.targets_to_urdf_radians(upper_targets, limits)

    for motor in bridge.MOTORS:
        assert lower_radians[motor] == pytest.approx(bridge.URDF_LIMITS_RAD[motor][0])
        assert upper_radians[motor] == pytest.approx(bridge.URDF_LIMITS_RAD[motor][1])
    assert limits["shoulder_pan"] == pytest.approx(tuple(map(math.degrees, (-1.91986, 1.91986))))
    assert limits["gripper"] == (0.0, 100.0)


def test_absolute_real_arm_enable_guard_reports_only_large_pose_errors(bridge):
    errors = bridge.excessive_enable_errors(
        {"shoulder_pan": 1.0, "shoulder_lift": 5.0, "gripper": 30.0},
        {"shoulder_pan": 4.0, "shoulder_lift": 20.0, "gripper": 20.0},
        max_error=10.0,
    )

    assert errors == {"shoulder_lift": 15.0}
    assert bridge.excessive_enable_errors({"shoulder_pan": 0.0}, {"shoulder_pan": 100.0}, 0.0) == {}


def test_fast_control_uses_faster_bounded_defaults_and_honors_overrides(bridge):
    assert bridge.resolve_control_speed(False, None, None) == (
        bridge.SAFE_MAX_RATE,
        bridge.SAFE_MAX_RELATIVE_TARGET,
    )
    assert bridge.resolve_control_speed(True, None, None) == (
        bridge.FAST_MAX_RATE,
        bridge.FAST_MAX_RELATIVE_TARGET,
    )
    assert bridge.resolve_control_speed(True, 40.0, 6.0) == (40.0, 6.0)


def test_calibration_identifies_the_old_single_pot_firmware(bridge):
    class SinglePotSerial:
        @staticmethod
        def readline():
            return b"raw=2054 filtered=2055 min=2050 center=2055 max=2060\n"

    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]

    with pytest.raises(RuntimeError, match="single-pot calibration stream"):
        bridge.learn_pot_calibrations(SinglePotSerial(), channels, 0.002, 50.0, "pot")


def test_plain_csv_value_count_ignores_boot_text(bridge):
    assert bridge.plain_csv_value_count("1,2,3,4,5,6") == 6
    assert bridge.plain_csv_value_count("boot mode (3,6)") is None
    assert bridge.plain_csv_value_count('{"values":[1,2,3]}') is None


def test_seven_pot_calibration_fails_fast_on_six_value_firmware(bridge):
    class OldFirmwareSerial:
        @staticmethod
        def readline():
            return b"100,200,300,400,500,600\n"

    channels = [
        bridge.Channel(pin=pin, motor=f"motor_{index}")
        for index, pin in enumerate(("32", "33", "34", "35", "25", "26", "27"))
    ]

    with pytest.raises(RuntimeError, match="received 6 CSV values.*requires 7"):
        bridge.learn_pot_calibrations(OldFirmwareSerial(), channels, 1.0, 50.0, "pot")


def test_recalibrate_only_selects_motors_or_gpio_pins(bridge):
    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]

    selected = bridge.parse_channel_selection("shoulder_lift,26", channels)

    assert [(channel.pin, channel.motor) for channel in selected] == [
        ("33", "shoulder_lift"),
        ("26", "gripper"),
    ]


def test_selective_gripper_learning_uses_the_sixth_csv_column(bridge):
    class SixPotSerial:
        sample = 0

        def readline(self):
            gripper = 500 + (self.sample % 10) * 100
            self.sample += 1
            return f"1000,1100,1200,1300,1400,{gripper}\n".encode()

    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]
    gripper = [channel for channel in channels if channel.motor == "gripper"]

    learned = bridge.learn_pot_calibrations(
        SixPotSerial(),
        channels,
        seconds=0.01,
        min_pot_range=50.0,
        gripper_input="pot",
        calibration_channels=gripper,
    )

    assert set(learned) == {"26"}
    assert learned["26"].low == 500.0
    assert learned["26"].high == 1400.0


def test_manual_calibration_collects_until_e_is_pressed_again(bridge, monkeypatch):
    class SixPotSerial:
        sample = 0
        reset_called = False

        def reset_input_buffer(self):
            self.reset_called = True

        def readline(self):
            value = 500 + self.sample * 100
            self.sample += 1
            return f"1000,1100,1200,1300,1400,{value}\n".encode()

    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]
    gripper = [channel for channel in channels if channel.motor == "gripper"]
    keys = iter(["e", None, None, None, None, None, "e"])
    monkeypatch.setattr(bridge, "read_key", lambda: next(keys, None))
    serial_port = SixPotSerial()

    learned = bridge.learn_pot_calibrations(
        serial_port,
        channels,
        seconds=0.0,
        min_pot_range=50.0,
        gripper_input="pot",
        calibration_channels=gripper,
    )

    assert serial_port.reset_called is True
    assert learned["26"].low == 500.0
    assert learned["26"].high == 1000.0


def test_selective_recalibration_preserves_other_ranges_and_saves_inversion(bridge, tmp_path, monkeypatch):
    channels = [
        bridge.Channel(pin=pin, motor=motor)
        for pin, motor in zip(bridge.DEFAULT_PINS, bridge.MOTORS, strict=True)
    ]
    original = {
        channel.pin: bridge.PotCalibration(low=float(index), center=100.0, high=200.0 + index)
        for index, channel in enumerate(channels)
    }
    path = tmp_path / "pots.json"
    bridge.save_pot_calibrations(path, channels, original)
    replacement = bridge.PotCalibration(low=300.0, center=1300.0, high=2300.0)

    def fake_learn(_ser, _channels, _seconds, _minimum, _gripper_input, calibration_channels=None):
        assert [channel.motor for channel in calibration_channels] == ["gripper"]
        return {"26": replacement}

    monkeypatch.setattr(bridge, "learn_pot_calibrations", fake_learn)
    args = SimpleNamespace(
        calibration_file=path,
        recalibrate=False,
        autocalib_seconds=10.0,
        min_pot_range=50.0,
        gripper_input="pot",
        no_save_calibration=False,
    )
    gripper = [channel for channel in channels if channel.motor == "gripper"]

    merged = bridge.make_pot_calibrations(
        args,
        object(),
        channels,
        recalibrate_channels=gripper,
        inverted_motors={"shoulder_lift"},
    )

    assert merged["32"] == original["32"]
    assert merged["26"] == replacement
    assert bridge.load_saved_inverted_motors(path, channels) == {"shoulder_lift"}
    assert json.loads(path.read_text(encoding="utf-8"))["inverted_motors"] == ["shoulder_lift"]
