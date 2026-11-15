"""Hardware-free checks for the seven-pot SO-101 + bounded slider path."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    script_directory = str(SCRIPTS)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    module_name = f"{name}_for_7dof_tests"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seven():
    return load_script("esp32_wh148_leslider_7dof_bridge")


def test_default_mapping_preserves_original_six_pins_and_adds_gpio27_slider(seven):
    assert [(item.pin, item.motor) for item in seven.default_channels()] == [
        ("32", "shoulder_pan"),
        ("33", "shoulder_lift"),
        ("34", "elbow_flex"),
        ("35", "wrist_flex"),
        ("25", "wrist_roll"),
        ("26", "gripper"),
        ("27", "slider"),
    ]
    assert seven.ROBOT_MOTORS == (
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
        "slider",
    )


def test_seven_value_firmware_csv_is_parsed_in_gpio_order(seven):
    values = seven.bridge.parse_pot_values(
        "100,200,300,400,500,600,700",
        list(seven.DEFAULT_PINS),
    )
    assert values == {
        "32": 100.0,
        "33": 200.0,
        "34": 300.0,
        "35": 400.0,
        "25": 500.0,
        "26": 600.0,
        "27": 700.0,
    }


def test_pot_file_keeps_original_joint_pins_and_slider_defaults(seven):
    original_path = seven.DEFAULT_CALIBRATION_FILE.parent / "wh148_leader_pot_calibration.json"
    original = json.loads(original_path.read_text(encoding="utf-8"))
    migrated = json.loads(seven.DEFAULT_CALIBRATION_FILE.read_text(encoding="utf-8"))
    for pin, original_entry in original["calibrations"].items():
        assert migrated["calibrations"][pin]["motor"] == original_entry["motor"]
        entry = migrated["calibrations"][pin]
        assert entry["low"] < entry["center"] < entry["high"]
    assert migrated["calibrations"]["27"] == {
        "motor": "slider",
        "low": 0.0,
        "center": 2047.5,
        "high": 4095.0,
    }
    assert set(migrated["inverted_motors"]) == {*original["inverted_motors"], "wrist_roll", "slider"}


def test_absolute_mapping_sends_gpio27_to_slider_and_gpio26_to_gripper(seven):
    channels = seven.default_channels()
    calibrations = {
        item.pin: seven.bridge.PotCalibration(low=0.0, center=2047.5, high=4095.0) for item in channels
    }
    latest = dict.fromkeys(seven.DEFAULT_PINS, 2047.5)
    latest["27"] = 1023.75
    latest["26"] = 3071.25
    limits = dict.fromkeys(seven.CHANNEL_MOTORS, (0.0, 100.0))

    inverted = seven.bridge.load_saved_inverted_motors(seven.DEFAULT_CALIBRATION_FILE, channels)
    targets = seven.bridge.compute_targets(
        latest,
        channels,
        calibrations,
        dict.fromkeys(seven.DEFAULT_PINS, 2047.5),
        dict.fromkeys(seven.CHANNEL_MOTORS, 50.0),
        limits,
        dict.fromkeys(seven.CHANNEL_MOTORS, 35.0),
        inverted,
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )

    assert targets["slider"] == pytest.approx(75.0)
    assert targets["gripper"] == pytest.approx(75.0)


def test_saved_mapping_reverses_gpio25_wrist_roll(seven):
    channels = seven.default_channels()
    calibrations = {
        item.pin: seven.bridge.PotCalibration(low=0.0, center=2047.5, high=4095.0) for item in channels
    }
    inverted = seven.bridge.load_saved_inverted_motors(seven.DEFAULT_CALIBRATION_FILE, channels)
    low_targets = seven.bridge.compute_targets(
        {"25": 0.0},
        channels,
        calibrations,
        {"25": 2047.5},
        {"wrist_roll": 50.0},
        {"wrist_roll": (0.0, 100.0)},
        {"wrist_roll": 35.0},
        inverted,
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )
    high_targets = seven.bridge.compute_targets(
        {"25": 4095.0},
        channels,
        calibrations,
        {"25": 2047.5},
        {"wrist_roll": 50.0},
        {"wrist_roll": (0.0, 100.0)},
        {"wrist_roll": 35.0},
        inverted,
        "absolute",
        "pot",
        0,
        100.0,
        0.0,
    )

    assert "wrist_roll" in inverted
    assert low_targets["wrist_roll"] == pytest.approx(100.0)
    assert high_targets["wrist_roll"] == pytest.approx(0.0)


def test_runtime_uses_full_multiturn_hardware_bounds_with_torque_off(seven):
    slider_module = sys.modules["so101_slider_follower"]
    calls = []
    write_kwargs = {}
    read_kwargs = {}

    class FakeBus:
        motors = {motor: object() for motor in slider_module.ALL_MOTORS}

        def sync_write(self, register, value, **_kwargs):
            calls.append((f"sync_write:{register}", value))

        def disable_torque(self, motors=None):
            calls.append(("disable_torque", tuple(motors or self.motors)))

        def configure_motors(self):
            calls.append(("configure_motors",))

        def write(self, register, motor, value, **_kwargs):
            calls.append((register, motor, value))
            write_kwargs[(register, motor)] = _kwargs

        def read(self, register, motor, **_kwargs):
            calls.append((f"read:{register}", motor))
            read_kwargs[(register, motor)] = _kwargs
            return 28 if register == "Phase" else 0

    robot = object.__new__(slider_module.SO101SliderFollower)
    robot.bus = FakeBus()
    robot.calibration = {
        slider_module.SLIDER: slider_module.MotorCalibration(
            id=7,
            drive_mode=1,
            homing_offset=0,
            range_min=-10000,
            range_max=14000,
        )
    }

    robot.configure()

    assert calls[0] == ("sync_write:Torque_Enable", 0)
    assert ("Min_Position_Limit", "slider", 0) in calls
    assert ("Max_Position_Limit", "slider", 0) in calls
    assert ("Min_Position_Limit", "slider", -10000) not in calls
    assert ("Max_Position_Limit", "slider", 14000) not in calls
    goal_index = calls.index(("Goal_Position", "slider", 0.0))
    velocity_index = calls.index(("Goal_Velocity", "slider", 2000))
    assert goal_index < velocity_index
    assert write_kwargs[("Goal_Position", "slider")]["normalize"] is False
    assert read_kwargs[("Present_Position", "slider")]["normalize"] is False
    assert calls[-1] == ("sync_write:Torque_Enable", 0)
    assert not any(call[0] == "enable_torque" for call in calls)


def test_cli_defaults_match_the_requested_fast_absolute_control(seven, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["esp32_wh148_leslider_7dof_bridge.py"])
    args = seven.parse_args()

    assert args.leader_port == "COM7"
    assert args.robot_port == "COM6"
    assert args.control_mode == "absolute"
    assert args.fast_control is True
    assert args.max_relative_target == pytest.approx(10.0)
    assert args.max_enable_error == pytest.approx(0.0)
    assert args.slider_id == 7


def test_legacy_robot_calibration_flag_is_now_slider_only(seven, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["esp32_wh148_leslider_7dof_bridge.py", "--calibrate-robot-only"],
    )
    assert seven.parse_args().calibrate_slider_only is True


def test_stock_calibration_lookup_uses_current_so_follower_namespace(seven):
    candidates = seven.stock_arm_calibration_path("unit_test").parents
    assert candidates[0].name == "so_follower"


def test_slider_only_calibration_never_addresses_arm_motors(seven, monkeypatch):
    slider_module = sys.modules["so101_slider_follower"]
    calls = []

    class FakeBus:
        def __init__(self, *, port, motors):
            self.port = port
            self.motors = motors
            self.is_connected = False

        def connect(self):
            self.is_connected = True

        def disable_torque(self, motors=None, **_kwargs):
            calls.append(("disable_torque", tuple(motors or self.motors)))

        def enable_torque(self, motors=None, **_kwargs):
            calls.append(("enable_torque", tuple(motors or self.motors)))

        def write(self, register, motor, value, **_kwargs):
            calls.append((f"write:{register}", (motor,), value))

        def read(self, register, motor, **_kwargs):
            calls.append((f"read:{register}", (motor,)))
            return 12 if register == "Phase" else 0

        def disconnect(self, **_kwargs):
            self.is_connected = False

    monkeypatch.setattr(slider_module, "FeetechMotorsBus", FakeBus)
    monkeypatch.setattr(slider_module, "capture_manual_slider_endpoints", lambda _bus: (-5000, 5000))

    calibration = seven.calibrate_slider_motor_only("TEST")

    assert calibration.id == 7
    assert calibration.range_min == -5000
    assert calibration.range_max == 5000
    assert calibration.drive_mode == 0
    assert calls
    assert all(motors == ("slider",) for _operation, motors, *_rest in calls)
    assert not any(operation == "enable_torque" for operation, *_rest in calls)
    assert not any(operation == "write:Goal_Position" for operation, *_rest in calls)
    assert ("write:Phase", ("slider",), 28) in calls
    assert ("write:Min_Position_Limit", ("slider",), 0) in calls
    assert ("write:Max_Position_Limit", ("slider",), 0) in calls


def test_manual_endpoints_preserve_user_direction_when_encoder_counts_decrease(seven):
    slider_module = sys.modules["so101_slider_follower"]
    calibration = slider_module.slider_calibration_from_endpoints(7, 14000, -10000)

    assert calibration.range_min == -10000
    assert calibration.range_max == 14000
    assert calibration.drive_mode == 1


def test_power_session_reference_rebases_origin_without_changing_direction_or_travel(seven):
    slider_module = sys.modules["so101_slider_follower"]
    saved = slider_module.MotorCalibration(
        id=7,
        drive_mode=1,
        homing_offset=0,
        range_min=-446,
        range_max=6158,
    )

    from_zero = slider_module.rebase_slider_calibration_for_session(saved, 5707, 0)
    assert from_zero.range_min == -897
    assert from_zero.range_max == 5707
    assert from_zero.drive_mode == 1
    assert slider_module.slider_position_from_raw(5707, from_zero) == pytest.approx(0.0)

    from_hundred = slider_module.rebase_slider_calibration_for_session(saved, 1000, 100)
    assert from_hundred.range_min == 1000
    assert from_hundred.range_max == 7604
    assert slider_module.slider_position_from_raw(1000, from_hundred) == pytest.approx(100.0)

    assert saved.range_min == -446
    assert saved.range_max == 6158


def test_power_session_reference_rejects_counter_range_that_cannot_fit_full_travel(seven):
    slider_module = sys.modules["so101_slider_follower"]
    saved = slider_module.MotorCalibration(
        id=7,
        drive_mode=1,
        homing_offset=0,
        range_min=-446,
        range_max=6158,
    )

    with pytest.raises(ValueError, match="outside the supported signed multi-turn range"):
        slider_module.rebase_slider_calibration_for_session(saved, -30000, 0)


def test_follower_session_reference_raw_latches_and_updates_only_bus_calibration(seven, monkeypatch):
    slider_module = sys.modules["so101_slider_follower"]
    saved = slider_module.MotorCalibration(
        id=7,
        drive_mode=1,
        homing_offset=0,
        range_min=-446,
        range_max=6158,
    )
    calls = []

    class FakeBus:
        is_connected = True

        def __init__(self):
            self.calibration = {slider_module.SLIDER: saved}

        def sync_write(self, register, value, **kwargs):
            calls.append(("sync_write", register, value, kwargs))

        def write(self, register, motor, value, **kwargs):
            calls.append(("write", register, motor, value, kwargs))

        def read(self, register, motor, **kwargs):
            calls.append(("read", register, motor, kwargs))
            if register in {"Present_Position", "Goal_Position"}:
                return 5707
            raise AssertionError(register)

    robot = object.__new__(slider_module.SO101SliderFollower)
    robot.bus = FakeBus()
    robot.calibration = {slider_module.SLIDER: saved}
    robot.slider_session_calibration = None
    robot.last_slider_raw_position = None
    monkeypatch.setattr(slider_module.time, "sleep", lambda _seconds: None)

    result = robot.reference_slider_session(0)

    assert result["raw_position"] == 5707
    assert result["normalized_position"] == pytest.approx(0.0)
    assert robot.calibration[slider_module.SLIDER] is saved
    assert robot.bus.calibration[slider_module.SLIDER].range_min == -897
    assert robot.bus.calibration[slider_module.SLIDER].range_max == 5707
    assert (
        "write",
        "Goal_Position",
        "slider",
        5707,
        {"normalize": False, "num_retry": 3},
    ) in calls
    assert calls[-1] == (
        "sync_write",
        "Torque_Enable",
        0,
        {"normalize": False, "num_retry": 5},
    )


def test_follower_session_validation_torques_off_when_raw_position_leaves_range(seven):
    slider_module = sys.modules["so101_slider_follower"]
    session = slider_module.MotorCalibration(
        id=7,
        drive_mode=1,
        homing_offset=0,
        range_min=-897,
        range_max=5707,
    )
    calls = []

    class FakeBus:
        def read(self, register, motor, **kwargs):
            calls.append(("read", register, motor, kwargs))
            return 5800

        def sync_write(self, register, value, **kwargs):
            calls.append(("sync_write", register, value, kwargs))

    robot = object.__new__(slider_module.SO101SliderFollower)
    robot.bus = FakeBus()
    robot.slider_session_calibration = session
    robot.last_slider_raw_position = None

    with pytest.raises(RuntimeError, match="outside this session's referenced range"):
        robot.validate_slider_session_position()

    assert calls[-1] == (
        "sync_write",
        "Torque_Enable",
        0,
        {"normalize": False, "num_retry": 5},
    )


def test_endpoint_only_wrapped_range_is_rejected(seven):
    slider_module = sys.modules["so101_slider_follower"]
    with pytest.raises(ValueError, match="live-unwrapped endpoints"):
        slider_module.slider_calibration_from_endpoints(7, 1000, 2528)


def test_combined_calibration_keeps_arm_values_unchanged(seven, tmp_path):
    slider_module = sys.modules["so101_slider_follower"]
    arm = {
        motor: slider_module.MotorCalibration(
            id=index,
            drive_mode=0,
            homing_offset=index * 10,
            range_min=index * 100,
            range_max=index * 100 + 50,
        )
        for index, motor in enumerate(slider_module.ARM_MOTORS, start=1)
    }
    slider = slider_module.MotorCalibration(
        id=7,
        drive_mode=0,
        homing_offset=0,
        range_min=-10000,
        range_max=14000,
    )
    output = tmp_path / "combined.json"

    seven.save_combined_slider_calibration("unit_test", arm, slider, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["shoulder_pan"]["homing_offset"] == 10
    assert payload["gripper"]["id"] == 6
    assert payload["slider"]["id"] == 7


def test_complete_saved_calibration_is_reused_without_hardware_comparison(seven, tmp_path):
    slider_module = sys.modules["so101_slider_follower"]
    payload = {
        motor: {
            "id": index,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": index * 100,
            "range_max": index * 100 + 50,
        }
        for index, motor in enumerate(slider_module.ALL_MOTORS, start=1)
    }
    payload[slider_module.SLIDER]["range_min"] = -10000
    payload[slider_module.SLIDER]["range_max"] = 14000
    calibration_path = tmp_path / "unit_test.json"
    calibration_path.write_text(json.dumps(payload), encoding="utf-8")
    slider_module.save_slider_safety_metadata(
        calibration_path,
        slider_module.MotorCalibration(**payload[slider_module.SLIDER]),
    )
    robot = seven.SO101SliderFollower(
        seven.SO101SliderFollowerConfig(
            port="TEST",
            id="unit_test",
            calibration_dir=tmp_path,
            import_arm_calibration=False,
        )
    )
    robot.bus.read_calibration = lambda: pytest.fail("hardware calibration must not be read")

    assert robot.is_calibrated is True

    class FakeConnectedBus:
        is_connected = False

        def connect(self):
            self.is_connected = True

        def disconnect(self, _disable_torque=True):
            self.is_connected = False

    robot.bus = FakeConnectedBus()
    robot.configure = lambda: None
    robot.calibrate = lambda: pytest.fail("complete saved calibration must be reused silently")
    robot.connect()
    assert robot.is_connected is True
    robot.disconnect()


def test_follower_exposes_seven_position_features_without_hardware(seven, tmp_path):
    robot = seven.SO101SliderFollower(
        seven.SO101SliderFollowerConfig(
            port="TEST",
            id="unit_test",
            calibration_dir=tmp_path,
            import_arm_calibration=False,
        )
    )

    assert tuple(robot.action_features) == tuple(f"{motor}.pos" for motor in seven.ROBOT_MOTORS)
    assert robot.bus.motors["slider"].id == 7


@pytest.mark.parametrize("slider_id", [1, 3, 6])
def test_slider_id_cannot_collide_with_arm(seven, tmp_path, slider_id):
    with pytest.raises(ValueError, match="collides"):
        seven.SO101SliderFollower(
            seven.SO101SliderFollowerConfig(
                port="TEST",
                id="unit_test",
                calibration_dir=tmp_path,
                import_arm_calibration=False,
                slider_id=slider_id,
            )
        )
