"""Run the real controller loops against a fake serial reader and motor bus."""
import importlib
from types import SimpleNamespace
import sys

import pytest


@pytest.mark.parametrize("seven", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_controller_telemetry_respects_pause_freshness_and_recenter(monkeypatch, seven, dry_run):
    six = importlib.import_module("esp32_wh148_leader_bridge")
    module = importlib.import_module("esp32_wh148_leslider_7dof_bridge") if seven else six
    names = list(six.MOTORS) + (["slider"] if seven else [])
    pins = list(module.DEFAULT_PINS)
    observed = []
    events = []
    state = {"step": -1, "now": 0.0}

    class Serial:
        def __init__(self, port, *args, **kwargs):
            assert port == "COM9"
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            events.append("serial closed")
        def reset_input_buffer(self):
            pass

    def read_line(*args):
        state["step"] += 1
        state["now"] = [1.0, 2.0, 2.1, 2.2, 3.0, 3.1][state["step"]]
        return ({} if state["step"] in (1, 4) else dict.fromkeys(pins, 3000)), "test"

    class Bus:
        is_connected = False
        def enable_torque(self):
            events.append(("enable", state["step"]))
        def disable_torque(self):
            events.append(("disable", state["step"]))

    class Robot:
        is_calibrated = True
        def __init__(self, *args):
            self.bus = Bus()
        @property
        def is_connected(self):
            return self.bus.is_connected
        def connect(self, **kwargs):
            assert not dry_run, "dry run must never connect the robot"
            self.bus.is_connected = True
        def get_observation(self):
            return {name + ".pos": 25.0 for name in names}
        def send_action(self, action):
            events.append(("action", state["step"], action))
            return action
        def disconnect(self):
            self.bus.is_connected = False
            events.append("disconnected")

    class Telemetry:
        def __init__(self, endpoint):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            events.append("telemetry closed")
        def update(self, robot, **kwargs):
            assert (robot is None) == dry_run
            observed.append((state["step"], kwargs.copy()))

    argv = ["test", "--leader-port", "COM9", "--robot-port", "COM6", "--telemetry", "--control-mode", "relative"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(six.serial, "Serial", Serial)
    monkeypatch.setattr(six, "read_pot_line", read_line)
    monkeypatch.setattr(six, "read_key", lambda: [None, "e", "e", None, None, "q"][state["step"]])
    clock = SimpleNamespace(perf_counter=lambda: state["now"], sleep=lambda _: None)
    monkeypatch.setattr(six, "time", clock)
    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module, "RobotTelemetry", Telemetry)
    monkeypatch.setattr(module, "apply_elbow_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(six, "robot_joint_limits", lambda *args: (-100, 100))
    if seven:
        monkeypatch.setattr(module, "robot_from_args", lambda *args: Robot())
        monkeypatch.setattr(module, "joint_limit", lambda *args: (-100, 100))
    else:
        monkeypatch.setattr(six, "SO101Follower", Robot)
    assert module.main() == 0
    states = dict(observed)
    assert not states[0]["enabled"]  # Startup stays paused.
    assert not states[1]["enabled"]  # E with stale data does not enable torque.
    assert not states[1]["input_fresh"]
    assert states[2]["enabled"]
    assert states[2]["targets"] == {}  # Never send the pre-recenter target.
    assert states[3]["input_fresh"]
    assert not states[4]["enabled"]  # Lost pots disable torque.
    assert "serial closed" in events and "telemetry closed" in events
    if not dry_run:
        assert ("enable", 1) not in events
        assert ("disable", 4) in events
        assert "disconnected" in events
        actions = [entry for entry in events if isinstance(entry, tuple) and entry[0] == "action"]
        assert len(actions) == 1 and actions[0][1] == 3
        assert all(value == 25 for value in actions[0][2].values())
