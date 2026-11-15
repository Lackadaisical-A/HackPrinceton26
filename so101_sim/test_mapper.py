import json
import math
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from mapper import Mapper, sensor_frame, slew

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE/"mapping.json").read_text())
LIMITS = {j.attrib["name"]: (float(j.find("limit").attrib["lower"]), float(j.find("limit").attrib["upper"]))
          for j in ET.parse(HERE/"model"/"so101_new_calib.urdf").findall("joint") if j.attrib["type"] == "revolute"}


def pose(deg, axis="y"):
    q = [math.cos(math.radians(deg)/2), 0, 0, 0]
    q[{"x": 1, "y": 2, "z": 3}[axis]] = math.sin(math.radians(deg)/2)
    return q


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.mapper = Mapper(CONFIG, LIMITS)
        self.mapper.calibrate({k: pose(0) for k in CONFIG["sensors"]})

    def test_shared_swing_does_not_bend_elbow_or_wrist(self):
        result = self.mapper.targets({k: pose(30) for k in CONFIG["sensors"]})
        self.assertAlmostEqual(result["shoulder_lift"], -math.pi/6)
        self.assertAlmostEqual(result["elbow_flex"], 0)
        self.assertAlmostEqual(result["wrist_flex"], 0)

    def test_elbow_and_forearm_twist_map_to_different_robot_joints(self):
        result = self.mapper.targets({"upper_arm": pose(0), "forearm": pose(40), "hand": pose(40)})
        self.assertAlmostEqual(result["elbow_flex"], -math.radians(40))
        self.assertAlmostEqual(result["wrist_flex"], 0)
        result = self.mapper.targets({"upper_arm": pose(0), "forearm": pose(40,"x"), "hand": pose(40,"x")})
        self.assertAlmostEqual(result["wrist_roll"], math.radians(40))

    def test_sign_invariance_limits_and_speed_bound(self):
        frame = {k: pose(170) for k in CONFIG["sensors"]}
        target = self.mapper.targets(frame)
        alternate = self.mapper.targets({k: [-v for v in q] for k,q in frame.items()})
        for joint in target:
            self.assertAlmostEqual(target[joint], alternate[joint])
            self.assertTrue(LIMITS[joint][0] <= target[joint] <= LIMITS[joint][1])
        next_pose = slew({k: 0 for k in target}, target, 1, .05)
        self.assertTrue(all(abs(v) <= .050001 for v in next_pose.values()))

    def test_stale_and_unsynchronized_inputs_are_rejected(self):
        data = {sensor: {"online":True,"timestamp_ms":10000,"hub_id":"one","quaternion":dict(zip("wxyz",pose(0)))}
                for sensor in CONFIG["sensors"].values()}
        self.assertEqual(len(sensor_frame(data, CONFIG, 10100)), 3)
        with self.assertRaises(ValueError): sensor_frame(data, CONFIG, 12000)
        data["wroom"]["timestamp_ms"] = 9500
        with self.assertRaises(ValueError): sensor_frame(data, CONFIG, 10100)


if __name__ == "__main__": unittest.main()
