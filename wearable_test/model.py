"""Orientation-driven arm geometry and two-pose sensor-mount calibration."""
import math

from so101_sim.mapper import inverse, multiply, normalize

ROLES = {"upper": "s3_imu1", "forearm": "s3_imu2", "hand": "wroom"}
AXES = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0),
        "-Y": (0, -1, 0), "+Z": (0, 0, 1), "-Z": (0, 0, -1)}
IDENTITY = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
DEFAULTS = {"upper_cm": 30., "forearm_cm": 26., "hand_cm": 18.,
            "bicep_from_elbow_pct": 25., "forearm_from_wrist_pct": 100/3,
            "hand_from_wrist_pct": 50., "axes": dict.fromkeys(ROLES, "Auto")}


def add(a, b): return tuple(x+y for x, y in zip(a, b))
def scale(v, s): return tuple(x*s for x in v)
def dot(a, b): return sum(x*y for x, y in zip(a, b))
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(v):
    n = math.sqrt(dot(v, v))
    if not math.isfinite(n) or n < 1e-6:
        raise ValueError("Calibration directions are degenerate; repeat both poses")
    return scale(v, 1/n)


def rotate(q, v):
    return multiply(multiply(q, (0, *v)), inverse(q))[1:]


def apply(matrix, v): return tuple(dot(row, v) for row in matrix)


def angle(a, b):
    return math.degrees(math.acos(max(-1., min(1., dot(unit(a), unit(b))))))


def quaternion_angle(a, b):
    return math.degrees(2*math.acos(min(1., abs(dot(normalize(a), normalize(b))))))


def stable_average(samples):
    if len(samples) < 20:
        raise ValueError("Not enough fresh readings; hold still and capture again")
    reference = normalize(samples[0])
    aligned = [scale(normalize(q), -1 if dot(reference, q) < 0 else 1) for q in samples]
    mean = normalize(tuple(sum(q[i] for q in aligned) for i in range(4)))
    if max(quaternion_angle(mean, q) for q in aligned) > 6:
        raise ValueError("Arm moved during capture; hold still and retry")
    return mean


def mounting_basis(reference, lowered=None, forward_axis="Auto"):
    """Rows map initial sensor coordinates to forward/left/up model axes.

    Gravity establishes up. Lowering a straight arm from forward to down is a
    positive rotation around model +Y, establishing the remaining heading.
    Each sensor's arbitrary game-vector yaw cancels in the relative rotation.
    """
    up = unit(rotate(inverse(reference), (0, 0, 1)))
    if forward_axis == "Auto":
        if lowered is None:
            raise ValueError("Capture pose B (arm lowered) to learn sensor directions")
        delta = normalize(multiply(inverse(reference), lowered))
        if delta[0] < 0:
            delta = scale(delta, -1)
        turn = math.degrees(2*math.acos(min(1., delta[0])))
        if not 45 <= turn <= 130:
            raise ValueError("Lower the straight arm 45-110 degrees without twisting; repeat pose B")
        hinge = unit(delta[1:])
        if abs(dot(hinge, up)) > .35:
            raise ValueError("Too much twisting between poses; lower in the forward/down plane")
        forward = unit(cross(hinge, up))
        down_direction = rotate(delta, forward)
        if dot(down_direction, up) > -.5:
            raise ValueError("Pose B must point the straight arm toward the floor")
    else:
        if forward_axis not in AXES:
            raise ValueError("Unknown sensor forward axis")
        raw = AXES[forward_axis]
        if abs(dot(raw, up)) > .7:
            raise ValueError(f"{forward_axis} points mostly vertically in pose A; choose the axis toward your fingers")
        forward = unit(add(raw, scale(up, -dot(raw, up))))
    side = unit(cross(up, forward))
    return (forward, side, up)


def validate_settings(settings):
    result = {**DEFAULTS, **settings}
    for key in ("upper_cm", "forearm_cm", "hand_cm"):
        result[key] = float(result[key])
        if not math.isfinite(result[key]) or not 5 <= result[key] <= 60:
            raise ValueError("Segment lengths must be 5-60 cm")
    for key in ("bicep_from_elbow_pct", "forearm_from_wrist_pct", "hand_from_wrist_pct"):
        result[key] = float(result[key])
        if not math.isfinite(result[key]) or not 0 <= result[key] <= 100:
            raise ValueError("Sensor positions must be 0-100% along their segment")
    result["axes"] = {**DEFAULTS["axes"], **result.get("axes", {})}
    if any(v not in ("Auto", *AXES) for v in result["axes"].values()):
        raise ValueError("Invalid sensor axis selection")
    return result


class ArmModel:
    def __init__(self, settings=None):
        self.settings = validate_settings(settings or {})
        self.reference = None
        self.mounts = None

    @property
    def calibrated(self):
        return self.mounts is not None and self.reference is not None

    def invalidate_reference(self):
        """A sensor reboot can change its world frame, not its strapped mounting."""
        self.reference = None

    def recenter(self, reference):
        """Known pose A restores world references while retaining learned mounts."""
        if self.mounts is None:
            raise ValueError("Capture A and B once to learn sensor mounting first")
        self.reference = {sensor:normalize(reference[sensor]) for sensor in ROLES.values()}

    def mounting_record(self):
        if self.mounts is None: raise ValueError("No learned mounting to save")
        return dict(version=1,settings=self.settings,mounts=self.mounts)

    def restore_mounting(self, record):
        """Never restore an old world reference across process/board restarts."""
        if record.get("version") != 1 or record.get("settings") != self.settings:
            raise ValueError("Saved mounting does not match current arm settings")
        mounts = {}
        for role in ROLES:
            basis = tuple(tuple(float(v) for v in row) for row in record["mounts"][role])
            if len(basis)!=3 or any(len(row)!=3 or not all(math.isfinite(v) for v in row) for row in basis):
                raise ValueError("Invalid saved mounting axes")
            if any(abs(dot(a,b)-(1 if i==j else 0))>1e-4 for i,a in enumerate(basis) for j,b in enumerate(basis)):
                raise ValueError("Saved mounting axes must be orthonormal")
            if dot(cross(basis[0],basis[1]),basis[2]) < .9999:
                raise ValueError("Invalid saved mounting handedness")
            mounts[role] = basis
        self.mounts,self.reference = mounts,None

    def calibrate(self, reference, lowered=None):
        mounts = {}
        for role, sensor in ROLES.items():
            try:
                mounts[role] = mounting_basis(normalize(reference[sensor]),
                    normalize(lowered[sensor]) if lowered else None, self.settings["axes"][role])
            except ValueError as error:
                raise ValueError(f"{role}: {error}") from error
        self.reference = dict(reference)
        self.mounts = mounts

    def geometry(self, frame=None):
        bases, boards = {}, {}
        for role, sensor in ROLES.items():
            if not self.calibrated or frame is None:
                bases[role] = boards[role] = IDENTITY
            else:
                delta = normalize(multiply(inverse(self.reference[sensor]), normalize(frame[sensor])))
                mount = self.mounts[role]
                # A bone axis in sensor coordinates is a row of the mount matrix.
                bases[role] = tuple(apply(mount, rotate(delta, axis)) for axis in mount)
                boards[role] = tuple(apply(mount, rotate(delta, axis)) for axis in IDENTITY)
        cfg = self.settings
        lengths = [cfg[k]/100 for k in ("upper_cm", "forearm_cm", "hand_cm")]
        origin = (0., 0., 0.)
        elbow = add(origin, scale(bases["upper"][0], lengths[0]))
        wrist = add(elbow, scale(bases["forearm"][0], lengths[1]))
        tip = add(wrist, scale(bases["hand"][0], lengths[2]))
        markers = {
            "upper": add(origin, scale(bases["upper"][0], lengths[0]*(1-cfg["bicep_from_elbow_pct"]/100))),
            "forearm": add(elbow, scale(bases["forearm"][0], lengths[1]*(1-cfg["forearm_from_wrist_pct"]/100))),
            "hand": add(wrist, scale(bases["hand"][0], lengths[2]*cfg["hand_from_wrist_pct"]/100)),
        }
        return {"joints": (origin, elbow, wrist, tip), "markers": markers, "bases": bases,
                "boards": boards, "elbow_deg": angle(bases["upper"][0], bases["forearm"][0]),
                "wrist_deg": angle(bases["forearm"][0], bases["hand"][0])}
