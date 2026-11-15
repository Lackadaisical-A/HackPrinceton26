import unittest
from joystick_calibration import JoystickCalibration, load_calibration


class CalibrationTests(unittest.TestCase):
    def test_measured_center_and_noise_produce_zero(self):
        cal = load_calibration()
        self.assertEqual((cal.center_x,cal.center_y),(1360,1810))
        for dx,dy in ((0,0),(-75,75),(75,-75)):
            self.assertEqual(cal.normalized(1360+dx,1810+dy),{"x":0.,"y":0.})

    def test_position_endpoints_center_and_reverse(self):
        cal = load_calibration()
        for raw,expected in ((0,100),(1810,50),(4095,0)):
            self.assertEqual(cal.position(raw,0,100),expected)
            self.assertEqual(cal.position(raw,0,100,reverse=True),100-expected)

    def test_asymmetric_travel_is_monotonic_and_full_scale(self):
        cal = load_calibration()
        for axis in ("x","y"):
            values = [cal.axis(raw,axis) for raw in range(4096)]
            self.assertEqual((values[0],values[-1]),(-1,1))
            self.assertEqual(values,sorted(values))

    def test_bad_configuration_or_samples_are_rejected(self):
        for kwargs in ({"center_x":0},{"center_y":4095},{"deadzone":1},{"deadzone":float("nan")}):
            with self.assertRaises(ValueError): JoystickCalibration(**kwargs)
        for raw in (-1,4096,float("nan")):
            with self.assertRaises(ValueError): JoystickCalibration().axis(raw,"y")


if __name__ == "__main__": unittest.main()
