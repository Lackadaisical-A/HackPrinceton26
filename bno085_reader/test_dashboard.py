import unittest
from unittest.mock import MagicMock, patch

from dashboard import Hub, open_sensor_port


class HubTests(unittest.TestCase):
    def test_serial_reset_lines_are_released_before_port_opens(self):
        connection = MagicMock()
        def open_check():
            self.assertIs(connection.dtr, False)
            self.assertIs(connection.rts, False)
            self.assertEqual(connection.port, "COM7")
        connection.open.side_effect = open_check
        with patch('dashboard.serial.Serial', return_value=connection) as factory:
            self.assertIs(open_sensor_port('COM7'), connection)
            factory.assert_called_once_with(port=None, baudrate=115200, timeout=.03, write_timeout=.5)
        connection.reset_input_buffer.assert_called_once()

    def test_failed_serial_open_closes_the_handle(self):
        connection = MagicMock()
        connection.open.side_effect = PermissionError('port busy')
        with patch('dashboard.serial.Serial', return_value=connection):
            with self.assertRaises(PermissionError):
                open_sensor_port('COM7')
        connection.close.assert_called_once()

    def test_all_three_routes_and_normalizes_without_overwriting(self):
        hub = Hub(cloud=False)
        hub.ingest("wroom", "2,0,0,0", now=10)
        hub.ingest("s3", "imu1,0,2,0,0", now=10)
        hub.ingest("s3", "imu2,0,0,2,0", now=10)
        payload = hub.payload(now=11)
        self.assertEqual(payload["active_sensors"], ["wroom", "s3_imu1", "s3_imu2"])
        for key, axis in (("wroom", "w"), ("s3_imu1", "x"), ("s3_imu2", "y")):
            sample = payload[f"sensors/{key}"]
            self.assertEqual(sample["quaternion"][axis], 1)
            self.assertEqual(sample["sequence"], 1)
            self.assertTrue(sample["online"])
        self.assertEqual(payload["sensors/wroom"]["port"], "COM7")
        self.assertEqual(payload["sensors/s3_imu1"]["port"], "COM8")

    def test_missing_and_stale_streams_are_not_reported_live(self):
        hub = Hub(cloud=False)
        hub.ingest("s3", "imu1,1,0,0,0", now=10)
        payload = hub.payload(now=11)
        self.assertFalse(payload["sensors/s3_imu2"]["online"])
        self.assertNotIn("quaternion", payload["sensors/s3_imu2"])
        self.assertFalse(hub.payload(now=13)["sensors/s3_imu1"]["online"])

    def test_other_messages_cannot_be_published_as_sensor_data(self):
        hub = Hub(cloud=False)
        for line in ("# HAPTIC ON 300", "imu3,1,0,0,0", "imu1,nan,0,0,0", "imu1,0,0,0,0"):
            with self.assertRaises(ValueError):
                hub.ingest("s3", line)
        self.assertTrue(all(s["sequence"] == 0 for s in hub.snapshot().values()))

    def test_joystick_is_separate_from_imu_and_expires(self):
        hub = Hub(cloud=False)
        hub.ingest_joystick("wroom", "# JOYSTICK,1000,2100,1800,1", now=10)
        sample = hub.joystick_snapshot()
        self.assertEqual((sample["x"], sample["y"], sample["pressed"]), (2100, 1800, True))
        self.assertTrue(all(s["sequence"] == 0 for s in hub.snapshot().values()))
        self.assertTrue(hub.payload(now=10.1)["controls/wroom_joystick"]["online"])
        self.assertFalse(hub.payload(now=10.4)["controls/wroom_joystick"]["online"])

    def test_invalid_joystick_data_does_not_refresh_previous_sample(self):
        hub = Hub(cloud=False)
        hub.ingest_joystick("wroom", "# JOYSTICK,1000,2048,2048,0", now=10)
        for prefix, line in (("s3", "# JOYSTICK,1001,2048,2048,0"),
                             ("wroom", "# JOYSTICK,1001,nan,2048,0"),
                             ("wroom", "# JOYSTICK,1001,4096,2048,0"),
                             ("wroom", "# JOYSTICK,1001,2048,2048,2")):
            with self.assertRaises(ValueError):
                hub.ingest_joystick(prefix, line, now=20)
        self.assertEqual(hub.joystick_snapshot()["seen"], 10)

    def test_cloud_joystick_retains_raw_and_adds_calibrated_position(self):
        hub = Hub(cloud=False)
        for raw,expected in ((0,1.),(1810,.5),(4095,0.)):
            hub.ingest_joystick("wroom",f"# JOYSTICK,1000,1360,{raw},0",now=10)
            value = hub.payload(now=10.1)["controls/wroom_joystick"]
            self.assertEqual((value["x"],value["y"]),(1360,raw))
            self.assertEqual(value["center"],{"x":1360,"y":1810})
            self.assertEqual(value["claw_open_fraction"],expected)
        stale = hub.payload(now=11)["controls/wroom_joystick"]
        self.assertFalse(stale["online"])
        self.assertEqual(stale["normalized"],{"x":0.,"y":0.})
        self.assertIsNone(stale["claw_open_fraction"])

    def test_joystick_reboot_and_disconnect_change_epoch(self):
        hub = Hub(cloud=False)
        hub.ingest_joystick("wroom", "# JOYSTICK,1000,2048,2048,0", now=10)
        epoch = hub.joystick_snapshot()["epoch"]
        hub.ingest_joystick("wroom", "# JOYSTICK,10,2048,2048,0", now=10.1)
        self.assertGreater(hub.joystick_snapshot()["epoch"], epoch)
        epoch = hub.joystick_snapshot()["epoch"]
        hub.invalidate_joystick()
        self.assertGreater(hub.joystick_snapshot()["epoch"], epoch)
        self.assertEqual(hub.joystick_snapshot()["sequence"], 0)


if __name__ == "__main__":
    unittest.main()
