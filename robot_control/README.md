# SO-101 motor-load feedback

For the calibrated Wearable Lab mapping and its new physical-arm controls, run
`.\sensor.ps1 wearable-robot -RobotPort COM6`. See [Wearable physical control](WEARABLE.md).
The `robot` command described below is the older dashboard/mapper and load-feedback mode.

The PC reads the SO-101's signed `Present_Load` register and sends short,
refreshed `VIBRATE` commands to the existing WROOM/DRV2605L. No ESP32 firmware
change is needed. This mode replaces IMU-angle vibration; all three IMUs
continue streaming and can optionally control the arm. Joystick claw control
requires the updated `wroom_io/wroom_io.ino` firmware.

## Connect and run

The arm needs its own USB servo-controller connection and the correct servo
power supply. COM7 is the WROOM; COM8 is the S3. The arm must appear as another
COM port. The previous LeRobot configuration used COM6, but verify it rather
than assuming that number. Close Arduino Serial Monitor and the old sensor
dashboard before starting this combined dashboard.

```powershell
.\sensor.ps1 ports
.\sensor.ps1 robot-check
.\sensor.ps1 robot
```

If several extra USB serial devices exist, explicitly select the arm:

```powershell
.\sensor.ps1 robot-check -RobotPort COM6
.\sensor.ps1 robot -RobotPort COM6
```

`robot-check` pings the six expected STS3215 motors and reads their position,
load and torque state. It does not write motor positions, enable/disable
torque, configure EEPROM, or rewrite calibration. It rejects COM7/COM8.

In the combined dashboard:

1. For a stationary load test, place the arm in a supported, clear pose and
   click **Hold current pose (load test)**. This enables the five arm motors
   at their existing positions, without enabling IMU control. Leave this off
   if you only want to inspect telemetry. The status shows torque ON/OFF.
2. With no external contact, click **Zero load at rest** if there is a resting
   load offset. The zero is fixed until clicked again; changing arm pose can
   change the load due to gravity and friction.
3. Click **Enable motor-load vibration**. Larger load means stronger continuous vibration.
4. The on-screen **LEFT** label is negative load relative to zero, **RIGHT** is
   positive. These labels are provisional until checked on your physical
   shoulder-pan motor. Use `-ReverseLoad` if the directions are reversed.
5. **Stop vibration** silences the coin. The firmware also expires each
   command after 300 ms if the host stops refreshing it.

If the vibration button is disabled and the bottom status says **ESP32 reports
DRV2605L not ready**, the ESP32 sent `DRV2605L=0`. This is different from a
Windows permission-denied error opening COM7. Both dashboard modes use the
same serial reader, now configured with DTR/RTS deasserted before opening to
avoid requesting an automatic ESP32 reset when switching modes. Run only one
dashboard/robot process at a time; the robot command includes the dashboard.

If the ESP32 still reports 0, check the driver's 3.3 V-compatible wiring, power and ground on the
WROOM's SDA21/SCL22 bus. Reset the WROOM after fixing the connection, because
its existing firmware initializes the driver at boot. IMU readings can still
work while the haptic driver is missing.

The coin vibrates continuously at the same intensity for equal load magnitudes
in either direction. Direction is shown on screen; there are no single/double
pulse patterns. Two coins would be needed for separate spatial cues.

Default feedback is from **shoulder_pan**, the base joint associated with
left/right rotation. Select another joint with `-Joint gripper`, etc. This is
servo effort in raw signed counts, **not measured Cartesian force or newtons**.
Motor effort includes acceleration, gravity, friction and controller action;
it does not uniquely identify external contact. Load feedback can be small
or zero while torque is disabled. Do not force an unpowered geared joint just
to generate a signal. See the [servo feedback documentation](https://www.waveshare.com/wiki/ST3215_Servo).

The defaults use absolute load relative to the captured zero, in either direction:

- Below 25 counts: no vibration, including immediately after a higher load.
- 25 to 95 counts: continuous intensity increases linearly from 30 to 127.
- At 95 counts or above: full 127/127 continuous intensity.

Commands are refreshed before their 300 ms expiry, so sustained load keeps
the coin running without programmed pauses. Missing/stale data stops vibration.
Thresholds use the latest sample without smoothing or a lower shutoff threshold.
Advanced configuration:

```powershell
uv run --python 3.12 robot_control/run.py --port COM6 --deadband 25 --full-scale 95 --reverse-load
```

## Optional IMU arm control

The arm starts **read only**. Use the same upper-arm/forearm/hand mounting
described in `so101_sim/README.md`. Put the robot in a suitable initial pose,
hold your wearable neutral pose, then click **Capture neutral + enable IMU arm
control**. The current physical joint positions become the robot's neutral;
it does not jump to URDF zero. Claw control is enabled separately as described
below; IMU control does not command the gripper.

The confirmed sensor placement and configured roles are:

| Sensor | Human placement | Mapper role |
| --- | --- | --- |
| WROOM / COM7 | Hand | `hand` |
| S3 imu1 / 0x4A / COM8 | Bicep / upper arm | `upper_arm` |
| S3 imu2 / 0x4B / COM8 | Forearm | `forearm` |

These are already the values in `so101_sim/mapping.json`; the dashboard panel
titles now show the body location. Capture neutral with all three IMUs fixed
in place. Consistent sensor-axis mounting is still necessary; physical
placement alone does not determine which way every robot joint should turn.

Motion requires matching saved homing offsets, position mode and valid joint
ranges. No calibration is written automatically. Goals are limited to saved
joint ranges, 10 degrees/second and 4 degrees ahead of measured position.
Stale IMUs or an absolute arm load of 650 counts pauses control. That threshold
is an initial conservative setting, not a calibrated collision detector.
The human-to-robot joint mapping still needs validation on this physical arm.

**Pause arm** holds the current pose. Closing the app attempts to hold the
current pose and leaves servo torque enabled if control was enabled. Keep
the arm's power switch reachable; a USB disconnect prevents software from
sending a new hold command. The independent servo position controllers may
continue toward their last commanded targets.

The dashboard alone owns COM7 and COM8; one worker owns the robot's port.
Do not run a second controller on that port. Firebase keeps the existing
`/live/sensors/*` streams and adds `/live/robot` with load counts, freshness,
selected joint, port and control status. Motor feedback is handled locally,
without a cloud round trip.

## Joystick claw control

WROOM joystick wiring: **VRx -> GPIO2, VRy -> GPIO4, SW -> GPIO5**, with 3.3 V
power and common GND. Upload the updated WROOM sketch to COM7; see its README
for the GPIO2 upload workaround. The S3 firmware does not need another change.

Run `.\sensor.ps1 robot -RobotPort COM6` (replace COM6 if the servo controller
has moved). Release the stick, then click **Enable claw position control (neutral = half open)**.
This checks the gripper's saved calibration and position mode,
captures its actual position, and enables only the gripper servo. Enabling
claw control does not enable the five IMU-controlled arm joints.

- Default Y axis sets **absolute position**: **Y=0 -> upper/max/open**, **Y=4095 -> lower/min/closed**.
  The measured neutral **Y=1810 -> halfway open**. Releasing the stick returns
  the claw toward halfway open, including after enabling; it does not hold an arbitrary opening.
- Center **X=1360 / Y=1810** and the 15% dead zone are shared with Wearable Lab
  and Firebase via `bno085_reader/joystick_calibration.json`. Each side scales
  separately so both ADC endpoints still reach full travel. Enabling requires
  the stick to be in its saved neutral zone; it does not replace the center with one sample.
- Targets slew at most 20% of calibrated travel per second, with at most 2%
  travel ahead of the measured position. Enabling does not jump to an endpoint.
- Pressing
  the joystick button pauses claw control; release the button and use the UI
  enable button to resume. **Pause claw** does the same thing.
- **Pause arm + claw** pauses both controls. An IMU-arm safety pause also pauses
  the claw. Starting a stationary arm-load test pauses claw control.
- Joystick data older than 300 ms, a WROOM reboot/reconnection, or gripper load
  magnitude at/above 350 counts pauses the claw. The load threshold is a starting
  setting in `robot_control/joystick.py`, not a measured force limit.

Change direction or select the other axis without uploading again:

```powershell
.\sensor.ps1 robot -RobotPort COM6 -ReverseClaw
.\sensor.ps1 robot -RobotPort COM6 -JoystickAxis x
```

Start with a small movement and confirm direction before grasping anything.
The saved gripper raw bounds are checked against homing offset and position
mode before enabling. A disconnected analog joystick is not electrically
detectable from ADC values alone; verify the dashboard readings before enabling.
Pausing holds the current position with torque on. Closing the app attempts
the same hold; losing the robot USB link prevents sending a new hold command.

The dashboard shows raw X/Y/button values while waiting for manual enable.
It publishes `/live/controls/wroom_joystick` with raw axes, pressed, sequence,
timestamp, connection epoch, online status and hub ID, plus `center`, `deadzone`,
`normalized` (-1..+1 axes) and `claw_open_fraction` (0..1; omitted when stale).
Collaborators should use the calibrated fields and check `online`/`pressed`.
`/live/robot` includes
claw enabled/status and, while active, gripper position, target and load. Control
runs locally through the existing COM7 owner, with no extra serial-port process.

## Copied files and tests

`vendor/lerobot` contains the Feetech motor bus and required support files from
`C:/Users/herma/Documents/lerobot/src/lerobot`, with the original Apache 2.0
license. The utility package initializer was reduced to omit unrelated ML
re-exports. The source repository is unchanged. `calibration.json` was copied
from the existing `so_follower/my_so101_follower` calibration cache;
`joint_mapping.json` is the original mapping reference (the active IMU mapping
is `so101_sim/mapping.json`). See `vendor/provenance.json` for hashes.

`test_robot.py` exercises direction cues, stale-data shutdown, calibration
refusal, motion bounds, read-only connection and robot failure handling.
Physical arm/load behavior cannot be claimed verified until `robot-check`
succeeds and the direction/thresholds have been tested on the connected arm.
