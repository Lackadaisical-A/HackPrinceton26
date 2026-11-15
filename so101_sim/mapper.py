"""Calibrated orientation proxies for a five-joint SO-101 pose preview."""
import math


def normalize(q):
    if len(q) != 4 or not all(math.isfinite(v) for v in q):
        raise ValueError("Invalid quaternion")
    norm = math.sqrt(sum(v*v for v in q))
    if norm < 1e-8:
        raise ValueError("Zero quaternion")
    return tuple(v/norm for v in q)


def inverse(q):
    return (q[0], -q[1], -q[2], -q[3])


def multiply(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return (w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v)


def twist(q, axis):
    # Extract the twist about a selected neutral-frame axis. This is a
    # control mapping, not a complete anatomical joint-angle estimator.
    component = q[{"x": 1, "y": 2, "z": 3}[axis]]
    if math.hypot(q[0], component) < 1e-8:
        return 0.0
    angle = 2*math.atan2(component, q[0])
    return (angle + math.pi) % (2*math.pi) - math.pi


def sensor_frame(data, config, now_ms):
    frame = {}
    stamps = []
    hubs = set()
    for role, sensor in config["sensors"].items():
        sample = data.get(sensor, {})
        stamp = sample.get("timestamp_ms", 0)
        age = (now_ms-stamp)/1000
        if not sample.get("online") or not 0 <= age <= config["max_sample_age_s"]:
            raise ValueError(f"{sensor}: missing/stale; start sensor.ps1 dashboard")
        try:
            frame[role] = normalize(tuple(sample["quaternion"][a] for a in "wxyz"))
        except (KeyError, TypeError) as error:
            raise ValueError(f"{sensor}: invalid quaternion") from error
        stamps.append(stamp)
        hubs.add(sample.get("hub_id"))
    if (max(stamps)-min(stamps))/1000 > config["max_sensor_skew_s"]:
        raise ValueError("Sensor timestamps are too far apart")
    if len(hubs) != 1:
        raise ValueError("Sensors belong to different dashboard sessions")
    return frame


class Mapper:
    def __init__(self, config, limits):
        self.config, self.limits = config, limits
        self.neutral = None

    def calibrate(self, frame):
        self.neutral = {key: normalize(q) for key, q in frame.items()}

    def targets(self, frame):
        if self.neutral is None:
            raise ValueError("Press C to capture neutral first")
        delta = {key: multiply(inverse(self.neutral[key]), normalize(q)) for key, q in frame.items()}
        upper, fore, hand = (delta[key] for key in ("upper_arm", "forearm", "hand"))
        rotations = {"upper": upper, "elbow": multiply(inverse(upper), fore),
                     "wrist": multiply(inverse(fore), hand),
                     "hand_from_upper": multiply(inverse(upper), hand)}
        values = {}
        for joint, spec in self.config["joints"].items():
            angle = twist(rotations[spec["rotation"]], spec["axis"])*spec["gain"] + math.radians(spec["offset_deg"])
            lower, upper_limit = self.limits[joint]
            values[joint] = max(lower, min(upper_limit, angle))
        return values


def slew(current, target, speed, dt):
    step = max(0, speed*min(.1, max(0, dt)))
    return {joint: value + max(-step, min(step, target[joint]-value)) for joint, value in current.items()}
