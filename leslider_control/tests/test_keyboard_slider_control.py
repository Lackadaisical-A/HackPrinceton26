"""Hardware-free tests for the ID-7 position-testing GUI/controller."""

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
    module_name = f"{name}_for_tests"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def position_gui():
    return load_script("keyboard_slider_control")


def test_zero_to_hundred_mapping_preserves_calibrated_direction(position_gui):
    normal = position_gui.SliderEndpoints(7, -10000, 14000, False)
    reversed_endpoints = position_gui.SliderEndpoints(7, -10000, 14000, True)

    assert position_gui.normalized_to_raw(0, normal) == -10000
    assert position_gui.normalized_to_raw(50, normal) == 2000
    assert position_gui.normalized_to_raw(100, normal) == 14000
    assert position_gui.normalized_to_raw(0, reversed_endpoints) == 14000
    assert position_gui.normalized_to_raw(50, reversed_endpoints) == 2000
    assert position_gui.normalized_to_raw(100, reversed_endpoints) == -10000
    assert position_gui.raw_to_normalized(8000, reversed_endpoints) == pytest.approx(25)


@pytest.mark.parametrize("value", [-0.01, 100.01])
def test_position_outside_zero_to_hundred_is_rejected(position_gui, value):
    endpoints = position_gui.SliderEndpoints(7, -10000, 14000, False)
    with pytest.raises(ValueError, match="0 through 100"):
        position_gui.normalized_to_raw(value, endpoints)


def test_session_reference_rebases_nonpersistent_multiturn_counter(position_gui):
    calibrated = position_gui.SliderEndpoints(7, -10000, 14000, False)

    from_zero = position_gui.rebase_endpoints(calibrated, current_raw=2000, reference_position=0)
    assert from_zero.raw_at_zero == 2000
    assert from_zero.raw_at_hundred == 26000

    from_hundred = position_gui.rebase_endpoints(calibrated, current_raw=10000, reference_position=100)
    assert from_hundred.raw_at_zero == -14000
    assert from_hundred.raw_at_hundred == 10000


def test_live_tracker_preserves_complete_encoder_turns(position_gui):
    tracker_class = sys.modules["so101_slider_follower"].UnwrappedPositionTracker
    tracker = tracker_class.from_encoder(3900)
    for encoder in (100, 1000, 2000, 3000, 4000, 100, 900):
        tracker.update(encoder)

    assert tracker.wraps == 2
    assert tracker.unwrapped == 9092


def test_loads_only_slider_entry_and_rejects_wrong_id(position_gui, tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(
            {
                "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": 12, "range_min": 1, "range_max": 2},
                "slider": {
                    "id": 7,
                    "drive_mode": 1,
                    "homing_offset": 0,
                    "range_min": -10000,
                    "range_max": 14000,
                },
            }
        ),
        encoding="utf-8",
    )
    slider_module = sys.modules["so101_slider_follower"]
    slider_module.save_slider_safety_metadata(
        path,
        slider_module.MotorCalibration(
            id=7,
            drive_mode=1,
            homing_offset=0,
            range_min=-10000,
            range_max=14000,
        ),
    )

    calibration = position_gui.load_slider_calibration(path, 7)
    assert calibration.id == 7
    with pytest.raises(ValueError, match="slider ID 7"):
        position_gui.load_slider_calibration(path, 8)


def test_calibration_without_phase28_safety_metadata_is_blocked(position_gui, tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "slider": {
                    "id": 7,
                    "drive_mode": 0,
                    "homing_offset": 0,
                    "range_min": -10000,
                    "range_max": 14000,
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="intentionally blocked"):
        position_gui.load_slider_calibration(path, 7)


def test_wrapped_endpoint_only_difference_is_rejected(position_gui):
    calibration = position_gui.MotorCalibration(
        id=7,
        drive_mode=1,
        homing_offset=0,
        range_min=1367,
        range_max=2965,
    )
    with pytest.raises(ValueError, match="minimum 4096"):
        position_gui.endpoints_from_calibration(calibration)


def test_indistinguishable_endpoint_capture_is_rejected(position_gui):
    calibration = position_gui.MotorCalibration(
        id=7,
        drive_mode=0,
        homing_offset=0,
        range_min=2000,
        range_max=2020,
    )
    with pytest.raises(ValueError, match="minimum 4096"):
        position_gui.endpoints_from_calibration(calibration)


def test_measured_1_6_turn_slider_mapping_is_accepted(position_gui):
    calibration = position_gui.MotorCalibration(
        id=7,
        drive_mode=0,
        homing_offset=0,
        range_min=-1884,
        range_max=4670,
    )
    endpoints = position_gui.endpoints_from_calibration(calibration)

    assert endpoints.travel_ticks == 6554
    assert position_gui.normalized_to_raw(0, endpoints) == -1884
    assert position_gui.normalized_to_raw(50, endpoints) == 1393
    assert position_gui.normalized_to_raw(100, endpoints) == 4670


def test_controller_uses_probe_watchdog_and_never_sends_full_target_first(position_gui, monkeypatch):
    calls = []
    created = []

    class FakeBus:
        def __init__(self, *, port, motors):
            self.port = port
            self.motors = motors
            self.is_connected = False
            self.registers = {
                "Torque_Enable": 0,
                "Operating_Mode": 0,
                "Phase": 28,
                "Torque_Limit": 200,
                "P_Coefficient": 16,
                "Minimum_Startup_Force": 16,
                "Present_Position": 0,
                "Goal_Position": 0,
                "Moving": 0,
            }
            created.append(self)

        def connect(self):
            self.is_connected = True

        def disable_torque(self, motors=None, **_kwargs):
            calls.append(("disable_torque", tuple(motors or self.motors)))
            self.registers["Torque_Enable"] = 0

        def enable_torque(self, motors=None, **_kwargs):
            calls.append(("enable_torque", tuple(motors or self.motors)))
            self.registers["Torque_Enable"] = 1

        def read(self, register, motor, **_kwargs):
            calls.append((f"read:{register}", (motor,)))
            return self.registers.get(register, 0)

        def write(self, register, motor, value, **_kwargs):
            calls.append((f"write:{register}", (motor,), value))
            self.registers[register] = value

        def disconnect(self, **_kwargs):
            self.is_connected = False

    monkeypatch.setattr(position_gui, "FeetechMotorsBus", FakeBus)
    monkeypatch.setattr(position_gui, "prepare_slider_position_mode", lambda bus: bus.disable_torque(["slider"]))
    endpoints = position_gui.SliderEndpoints(7, -10000, 14000, False)
    controller = position_gui.SliderPositionController("TEST", endpoints)

    controller.connect()
    assert controller.bus.registers["Torque_Limit"] == position_gui.DEFAULT_TORQUE_LIMIT
    assert controller.bus.registers["P_Coefficient"] == position_gui.POSITION_P_COEFFICIENT
    assert controller.bus.registers["Minimum_Startup_Force"] == position_gui.MINIMUM_STARTUP_FORCE
    with pytest.raises(RuntimeError, match="Set the current physical endpoint"):
        controller.move_to(25, 300)
    session = controller.set_reference(0)
    assert session.raw_at_zero == 0
    assert session.raw_at_hundred == 24000

    controller.bus.registers["Present_Position"] = -3272
    signed_status = controller.read_status()
    assert signed_status.raw_position == -3272
    assert signed_status.encoder_position == 824
    controller.bus.registers["Present_Position"] = 0

    target = controller.move_to(25, 600)
    assert target == 6000
    assert controller.bus.registers["Torque_Enable"] == 1
    assert controller.bus.registers["Goal_Time"] == 0
    assert controller.bus.registers["Goal_Position"] == 0
    controller.motion.started_at -= position_gui.SETTLE_SECONDS
    _status, event = controller.monitor_motion()
    assert event is None
    assert controller.bus.registers["Goal_Position"] == position_gui.PROBE_TICKS
    assert not any(call == ("write:Goal_Position", ("slider",), 6000) for call in calls)

    # Simulate feedback moving the opposite direction while still inside the
    # physical envelope. The watchdog must immediately torque off.
    controller.stop()
    controller.bus.registers["Present_Position"] = 12000
    controller.move_to(25, 300)
    controller.motion.started_at -= position_gui.SETTLE_SECONDS
    _status, event = controller.monitor_motion()
    assert event is None
    controller.bus.registers["Present_Position"] = 12000 + position_gui.WRONG_WAY_HARD_TICKS
    _status, event = controller.monitor_motion()
    assert event is not None
    assert event.kind == "fault"
    assert "WRONG DIRECTION" in event.message
    assert controller.bus.registers["Torque_Enable"] == 0

    # Simulate correct feedback through every small checkpoint. Completion
    # automatically removes torque instead of holding indefinitely.
    controller.bus.registers["Present_Position"] = 0
    target = controller.move_to(1, 300)
    assert target == 240
    controller.motion.started_at -= position_gui.SETTLE_SECONDS
    _status, event = controller.monitor_motion()
    assert event is None
    controller.bus.registers["Present_Position"] = 10
    _status, event = controller.monitor_motion()
    assert event is None
    while controller.motion is not None:
        controller.bus.registers["Present_Position"] = controller.motion.checkpoint_raw
        _status, event = controller.monitor_motion()
    assert event is not None
    assert event.kind == "success"
    assert controller.bus.registers["Torque_Enable"] == 0
    controller.close()

    assert set(created[0].motors) == {"slider"}
    assert created[0].motors["slider"].id == 7
    assert all(call[1] == ("slider",) for call in calls)
    assert not any(call[0] in {"write:Min_Position_Limit", "write:Max_Position_Limit"} for call in calls)
    assert not any("shoulder" in repr(call) for call in calls)


def test_probe_expands_to_take_up_backlash_then_stops_without_motion(position_gui, monkeypatch):
    now = [100.0]

    class FakeBus:
        def __init__(self, *, port, motors):
            self.port = port
            self.motors = motors
            self.is_connected = False
            self.registers = {
                "Torque_Enable": 0,
                "Operating_Mode": 0,
                "Phase": 28,
                "Torque_Limit": position_gui.DEFAULT_TORQUE_LIMIT,
                "P_Coefficient": position_gui.POSITION_P_COEFFICIENT,
                "Minimum_Startup_Force": position_gui.MINIMUM_STARTUP_FORCE,
                "Present_Position": 0,
                "Goal_Position": 0,
                "Moving": 0,
            }

        def connect(self):
            self.is_connected = True

        def disable_torque(self, motors=None, **_kwargs):
            self.registers["Torque_Enable"] = 0

        def enable_torque(self, motors=None, **_kwargs):
            self.registers["Torque_Enable"] = 1

        def read(self, register, motor, **_kwargs):
            return self.registers.get(register, 0)

        def write(self, register, motor, value, **_kwargs):
            self.registers[register] = value

        def disconnect(self, **_kwargs):
            self.is_connected = False

    monkeypatch.setattr(position_gui, "FeetechMotorsBus", FakeBus)
    monkeypatch.setattr(position_gui, "prepare_slider_position_mode", lambda bus: bus.disable_torque(["slider"]))
    monkeypatch.setattr(position_gui.time, "monotonic", lambda: now[0])
    controller = position_gui.SliderPositionController(
        "TEST", position_gui.SliderEndpoints(7, 0, 6554, False)
    )
    controller.connect()
    controller.set_reference(0)
    controller.move_to(50, 300)

    expected_probe = position_gui.PROBE_TICKS
    assert controller.bus.registers["Goal_Position"] == 0
    now[0] += position_gui.SETTLE_SECONDS + 0.001
    _status, event = controller.monitor_motion()
    assert event is None
    assert controller.bus.registers["Goal_Position"] == expected_probe
    probe_started_at = now[0]
    while expected_probe < position_gui.MAX_PROBE_TICKS:
        now[0] += position_gui.PROBE_STEP_SECONDS + 0.001
        _status, event = controller.monitor_motion()
        assert event is None
        expected_probe = min(
            position_gui.MAX_PROBE_TICKS,
            expected_probe + position_gui.PROBE_STEP_TICKS,
        )
        assert controller.bus.registers["Goal_Position"] == expected_probe

    now[0] = probe_started_at + position_gui.PROBE_TIMEOUT_SECONDS
    _status, event = controller.monitor_motion()
    assert event is not None
    assert event.kind == "fault"
    assert "NO MOVEMENT" in event.message
    assert f"{position_gui.MAX_PROBE_TICKS}-tick" in event.message
    assert controller.bus.registers["Torque_Enable"] == 0


def test_wrong_way_requires_sustained_or_hard_reverse_motion(position_gui):
    motion = position_gui.MotionState(
        requested_position=100,
        target_raw=-3272,
        start_raw=3404,
        direction=-1,
        checkpoint_raw=3324,
        best_error=6676,
        started_at=10.0,
        last_progress_at=10.0,
        probe_distance=80,
        last_probe_at=10.0,
        stage="probe",
    )

    assert not position_gui.SliderPositionController._wrong_way_is_confirmed(
        motion, position_gui.WRONG_WAY_TICKS, 10.0
    )
    assert not position_gui.SliderPositionController._wrong_way_is_confirmed(
        motion,
        position_gui.WRONG_WAY_TICKS,
        10.0 + position_gui.WRONG_WAY_CONFIRM_SECONDS / 2,
    )
    assert position_gui.SliderPositionController._wrong_way_is_confirmed(
        motion,
        position_gui.WRONG_WAY_TICKS,
        10.0 + position_gui.WRONG_WAY_CONFIRM_SECONDS + 0.001,
    )

    motion.wrong_way_since = None
    assert position_gui.SliderPositionController._wrong_way_is_confirmed(
        motion, position_gui.WRONG_WAY_HARD_TICKS, 20.0
    )
