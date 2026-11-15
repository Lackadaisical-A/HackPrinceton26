"""Feedback semantics and the real ZMQ -> WebSocket path, with no hardware."""
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
import zmq
from websockets.asyncio.client import connect

from leslider_control.telemetry import JOINTS, RobotTelemetry, robot_message

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
from paul_bridge.relay import Relay


def test_measured_feedback_has_canonical_order_signed_load_and_separate_targets():
    positions = dict(reversed(list(zip(JOINTS, [10, 20, 30, 40, 50, 60]))))
    positions["slider"] = 75
    targets = dict.fromkeys(JOINTS, 5)
    targets["slider"] = 80
    result = robot_message(positions, dict(zip(JOINTS, [-1000, 0, 500, 1000, 1023, -1023])),
                           targets=targets, enabled=False, input_fresh=False, preview=False,
                           input_source="imu", t=123)
    assert result["pos"] == [10, 20, 30, 40, 50, 60]
    assert result["load"] == [-1, 0, .5, 1, 1, -1]
    assert result["control"]["target"] == [5] * 6
    assert result["control"]["input"] == "imu"
    assert result["slider"] == {"pos": 75, "target": 80}


def test_nonfinite_feedback_is_not_published():
    with pytest.raises(ValueError):
        robot_message(dict.fromkeys(JOINTS, float("nan")), None, targets={}, enabled=False,
                      input_fresh=False, preview=False, input_source="potentiometer", t=1)


class CaptureSocket:
    def __init__(self):
        self.frames = []

    def send_string(self, frame, flags):
        self.frames.append(json.loads(frame.split(" ", 1)[1]))


@pytest.fixture
def capture():
    with RobotTelemetry("tcp://127.0.0.1:0") as publisher:
        original = publisher.socket
        publisher.socket = CaptureSocket()
        yield publisher
        publisher.socket = original


def test_dry_run_does_not_fabricate_load_or_refresh_stale_targets(capture):
    targets = dict.fromkeys(JOINTS, 12)
    capture.update(None, targets=targets, enabled=False, input_fresh=True, now=1)
    capture.update(None, targets=targets, enabled=False, input_fresh=False, now=2)
    capture.update(None, targets={}, enabled=False, input_fresh=True, now=3)
    assert len(capture.socket.frames) == 1
    frame = capture.socket.frames[0]
    assert frame["load"] is None
    assert frame["control"]["feedback"] == "preview"
    assert not frame["control"]["enabled"]


def test_read_only_measurements_while_paused_and_no_more_than_ten_reads_per_second(capture):
    reads = []

    def observation():
        reads.append("position")
        return {name + ".pos": 20 for name in JOINTS}

    def load(register, motors, **kwargs):
        assert register == "Present_Load" and motors == list(JOINTS)
        assert kwargs == {"normalize": False, "num_retry": 0}
        return dict.fromkeys(JOINTS, -200)

    robot = SimpleNamespace(get_observation=observation, bus=SimpleNamespace(sync_read=load))
    for now in (1, 1.02, 1.05, 1.11):
        capture.update(robot, targets=dict.fromkeys(JOINTS, 80), enabled=False, input_fresh=False, now=now)
    assert len(reads) == 2
    frame = capture.socket.frames[-1]
    assert frame["pos"] == [20] * 6  # Not the requested 80-degree target.
    assert frame["load"] == [-.2] * 6
    assert frame["control"]["feedback"] == "measured"
    assert not frame["control"]["input_fresh"]


def test_failed_position_read_does_not_republish_an_old_measurement(capture):
    def broken():
        raise ConnectionError("motor disconnected")
    capture.update(SimpleNamespace(get_observation=broken), targets={}, enabled=True, input_fresh=True, now=1)
    assert capture.socket.frames == []


def test_failed_load_read_reports_unavailable_instead_of_zero(capture):
    def broken(*args, **kwargs):
        raise ConnectionError("no load reply")
    robot = SimpleNamespace(get_observation=lambda: {name + ".pos": 2 for name in JOINTS},
                            bus=SimpleNamespace(sync_read=broken))
    capture.update(robot, targets={}, enabled=False, input_fresh=True, now=1)
    assert capture.socket.frames[0]["load"] is None


def test_robot_and_vision_share_real_websocket_and_commands_return_to_vision():
    async def run():
        with zmq.Context() as context, RobotTelemetry("tcp://127.0.0.1:0") as publisher:
            vision = context.socket(zmq.PUB)
            vision.bind("tcp://127.0.0.1:0")
            commands = context.socket(zmq.SUB)
            commands.subscribe(b"command ")
            async with Relay([publisher.endpoint, vision.last_endpoint.decode()], "tcp://127.0.0.1:0", port=0) as relay:
                commands.connect(relay.command_endpoint)
                async with connect(relay.url, origin="http://127.0.0.1:5173") as browser:
                    seen = {}
                    deadline = time.monotonic() + 5
                    while len(seen) < 2 and time.monotonic() < deadline:
                        publisher.update(None, targets=dict.fromkeys(JOINTS, 42), enabled=False, input_fresh=True)
                        vision.send_string('vision {"t":1,"status":"no_target"}')
                        try:
                            frame = await asyncio.wait_for(browser.recv(), .2)
                            topic, payload = frame.split(" ", 1)
                            seen[topic] = json.loads(payload)
                        except TimeoutError:
                            pass
                        await asyncio.sleep(.02)
                    assert set(seen) == {"robot", "vision"}
                    assert seen["robot"]["control"]["feedback"] == "preview"
                    command = 'command {"t":1,"type":"select_target","label":"cup"}'
                    received = None
                    while time.monotonic() < deadline:
                        await browser.send(command)
                        await asyncio.sleep(.05)
                        if commands.poll(0):
                            received = commands.recv_string()
                            break
                    assert received == command
            commands.close(linger=0)
            vision.close(linger=0)
    asyncio.run(run())
