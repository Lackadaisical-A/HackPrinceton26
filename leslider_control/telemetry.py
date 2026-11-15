"""Controller-independent arm telemetry for the PAUL console.

The controller remains the sole owner of the motor bus. Call update on its
thread; ZMQ sends never wait for a browser. An IMU controller can use the same
publisher with input_source='imu' without changing vision or the console.
"""
from __future__ import annotations

import json
import math
import time

import zmq

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
DEFAULT_ENDPOINT = "tcp://127.0.0.1:5558"


def add_telemetry_args(parser):
    parser.add_argument("--telemetry", action="store_true", help="Publish arm feedback to the PAUL web console.")
    parser.add_argument("--telemetry-endpoint", default=DEFAULT_ENDPOINT)


def disconnect_if_connected(robot):
    """Also clean up a connection that failed partway through startup."""
    if robot.bus.is_connected:
        robot.disconnect()


def robot_message(positions, loads, *, targets, enabled, input_fresh, preview, input_source, t):
    values = [float(positions[name]) for name in JOINTS]
    load = None if loads is None else [max(-1.0, min(1.0, float(loads[name]) / 1000.0)) for name in JOINTS]
    target = [float(targets[name]) for name in JOINTS] if all(name in targets for name in JOINTS) else None
    if not all(math.isfinite(v) for v in values + (target or []) + (list(loads.values()) if loads else [])):
        raise ValueError("Non-finite robot telemetry")
    message = {
        "t": t, "pos": values, "load": load,
        "control": {
            "input": input_source, "enabled": enabled, "input_fresh": input_fresh,
            "feedback": "preview" if preview else "measured", "target": target,
        },
    }
    if "slider" in positions:
        message["slider"] = {"pos": float(positions["slider"]), "target": targets.get("slider")}
    return message


class RobotTelemetry:
    def __init__(self, endpoint=DEFAULT_ENDPOINT, *, input_source="potentiometer", rate_hz=10):
        self.input_source = input_source
        self.interval = 1.0 / rate_hz
        self.next_sample = 0.0
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.setsockopt(zmq.SNDHWM, 2)
        self.socket.setsockopt(zmq.LINGER, 0)
        try:
            self.socket.bind(endpoint)
        except Exception:
            self.close()
            raise
        self.endpoint = self.socket.last_endpoint.decode()
        self.last_error = None

    def update(self, robot, *, targets, enabled, input_fresh, now=None):
        now = time.perf_counter() if now is None else now
        if now < self.next_sample:
            return
        self.next_sample = now + self.interval
        # No synthetic pose or refreshed stale pot targets during a dry run.
        if robot is None and (not input_fresh or not all(name in targets for name in JOINTS)):
            return
        try:
            sampled_at = time.time()
            loads = None
            if robot is None:
                positions = targets
            else:
                # Timestamp before reading, so a slow bus cannot make an old sample look new.
                positions = {key.removesuffix(".pos"): value for key, value in robot.get_observation().items()}
                try:
                    # The driver already decodes STS3215 sign-magnitude. 1000 counts = nominal full scale.
                    loads = robot.bus.sync_read("Present_Load", list(JOINTS), normalize=False, num_retry=0)
                except (ConnectionError, OSError, RuntimeError):
                    pass  # Position remains useful; absent load is displayed as unavailable.
            message = robot_message(
                positions, loads, targets=targets, enabled=enabled, input_fresh=input_fresh,
                preview=robot is None, input_source=self.input_source, t=sampled_at,
            )
            self.socket.send_string("robot " + json.dumps(message, separators=(",", ":"), allow_nan=False), zmq.NOBLOCK)
            self.last_error = None
        except zmq.Again:
            pass
        except (ConnectionError, OSError, RuntimeError, ValueError, KeyError) as exc:
            # Telemetry cannot take control away from the operator. Missing feedback goes stale in the UI.
            if str(exc) != self.last_error:
                print(f"\nConsole telemetry unavailable: {exc}")
            self.last_error = str(exc)

    def close(self):
        self.socket.close(linger=0)
        self.context.term()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
