"""Publish already-sampled wearable feedback; never open or read the servo bus."""
import json
import logging
import math

import zmq

from robot_control.arm import BODY, JOINTS
from leslider_control.telemetry import robot_message


def console_positions(raw, limits, mapping, direction):
    """Invert raw_targets: URDF degrees for the arm, calibrated percent for claw."""
    result = {name:math.degrees(value) for name,value in mapping.urdf_angles(raw,limits,direction).items()}
    for name in JOINTS:
        if name in BODY: continue
        minimum, maximum = limits[name]
        ratio = (raw[name] - minimum) / (maximum - minimum)
        result[name] = 100 * ratio
    return result


class WearableTelemetry:
    def __init__(self, endpoint):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.setsockopt(zmq.SNDHWM, 2)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.next_sample = 0.
        self.last_error = None
        try:
            self.socket.bind(endpoint)
        except Exception:
            self.close()
            raise

    def publish(self, *, positions, loads, targets, limits, mapping, direction,
                enabled, input_fresh, sampled_at, now, wearable_snapshot=None, sensor_now=None, slider=None):
        if now < self.next_sample:
            return
        self.next_sample = now + .1
        try:
            pose = console_positions(positions, limits, mapping, direction)
            # Uncontrolled joints retain their measured position in the target display.
            target = console_positions({**positions, **targets}, limits, mapping, direction) if targets else {}
            message = robot_message(pose, loads, targets=target, enabled=enabled,
                                    input_fresh=input_fresh, preview=False, input_source='imu', t=sampled_at)
            if slider and slider['referenced'] and slider['pos'] is not None:
                message['slider'] = {key:slider[key] for key in ('pos','target')}
            self.socket.send_string('robot ' + json.dumps(message, separators=(',', ':'), allow_nan=False), zmq.NOBLOCK)
            if wearable_snapshot is not None and sensor_now is not None:
                sample = wearable_snapshot['wroom']['samples']['wroom']
                age = sensor_now - sample['seen']
                if sample['q'] is not None and 0 <= age < .3:
                    imu = {'t': sampled_at - age, 'q': list(sample['q'])}
                    self.socket.send_string('imu ' + json.dumps(imu, separators=(',', ':'), allow_nan=False), zmq.NOBLOCK)
            self.last_error = None
        except zmq.Again:
            pass
        except Exception as error:
            # A console failure must never interrupt control or turn a stale sample fresh.
            if str(error) != self.last_error:
                logging.getLogger('wearable').warning('Console telemetry unavailable: %s', error)
            self.last_error = str(error)

    def close(self):
        self.socket.close(linger=0)
        self.context.term()
