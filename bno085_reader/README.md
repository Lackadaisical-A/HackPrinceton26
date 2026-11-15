# BNO085 live 3D viewer

## All three IMUs + Firebase

Run from the repository root:

```powershell
.\sensor.ps1 dashboard
```

This opens one dashboard for COM7 (WROOM) and COM8 (S3), with three 3D views.
Close other serial viewers/monitors/bridges first. One process owns both ports
and uploads their readings using your local Firebase config.

Firebase readings are under `/live/sensors/wroom`, `/live/sensors/s3_imu1`,
and `/live/sensors/s3_imu2`. Each has `quaternion` (w,x,y,z), `timestamp_ms`,
`sequence`, `port`, `online`, and `hub_id`. `/live/active_sensors` lists the
three current IDs; older unrelated sensor paths are left in place. Check
timestamp freshness as well as online: an abruptly killed host cannot update
its offline flag. Publishing runs separately from serial reading at up to
10 updates/sec; local visualization follows the original sample streams.

**Zero all 3D views** sets each displayed pose to neutral. Cloud quaternions
remain raw. These IMUs have independent initial headings; zeroing aligns their
current poses visually, not their physical mounting axes.

**Enable WROOM vibration at current pose** enables the existing proportional
neutral-pose control using the same COM7 connection. It is disabled until
the WROOM identifies the DRV2605L and provides fresh IMU data. Use **Stop
vibration** to turn it off. Closing the dashboard requests STOP.

This dashboard streams live data; it does not start a local recording.
For different ports or offline viewing:

```powershell
uv run bno085_reader/dashboard.py --wroom-port COM7 --s3-port COM8 --no-firebase
```

## Single-sensor USB viewer

Upload `bno085_reader.ino` with the Adafruit BNO08x Arduino library installed.
For a classic ESP32 DevKit, connect SDA to GPIO 21, SCL to GPIO 22,
GND to GND, and the breakout's power input to 3.3 V. Configure the breakout for I2C.

Close Arduino Serial Monitor/Plotter, then run from the repository root:

```powershell
uv run bno085_reader/viewer.py
```

Choose the ESP32 COM port and click **Connect**. The 3D board follows the
sensor's quaternion orientation. **Zero orientation** makes the current pose
neutral. **Demo** shows synthetic motion without hardware.

The viewer uses Python's built-in Tkinter and pySerial, installed automatically
by uv. The serial format is one `w,x,y,z` quaternion per line at 115200 baud,
matching the Arduino sketch. Text startup messages are ignored.

This displays rotation, not position. The sketch's game rotation vector does
not use magnetic north, so heading can drift. Model axes assume alignment with
the sensor's axes; physically mounting a board differently changes that alignment.
