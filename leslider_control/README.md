# LeSlider and potentiometer control

For potentiometers **with vision and the web console**, run `.\demo.ps1`
from the repo root. Use `-DryRun` for previews without a robot connection;
`-Controller pots` selects six channels. See [the integrated test guide](../POT_TEST.md).
The commands below remain available for standalone control.

Copied from your local `Documents/lerobot` checkout. These commands use the
drivers and calibration files inside this repo; the original checkout and its
Python environment are not required. Python 3.12 and dependencies are managed
by `uv` on first run.

## Choose the Arduino sketch

| Hardware you want to control | Sketch to open in Arduino IDE |
|---|---|
| SO-101 arm and gripper, six pots | `arduino/esp32_wh148_leader_arm/esp32_wh148_leader_arm.ino` |
| SO-101 arm, gripper and LeSlider, seven pots | `arduino/esp32_wh148_leslider_7dof/esp32_wh148_leslider_7dof.ino` |

Select **ESP32 Dev Module** for a classic ESP32/WROOM and select that board's
actual COM port. The sketches use only Arduino's ESP32 core; no extra sensor
library is needed. They deliberately reject an ESP32-S3 build because its ADC
pins differ. Nothing is automatically flashed by these scripts.

Each WH148 potentiometer: one outer leg to **3.3 V**, the other to **GND**, and
the middle/wiper leg to the GPIO below. All grounds are shared. Use 3.3 V,
not 5 V, for these ESP32 ADC inputs.

| GPIO | Joint |
|---|---|
| 32 | Shoulder pan |
| 33 | Shoulder lift |
| 34 | Elbow flex |
| 35 | Wrist flex |
| 25 | Wrist roll |
| 26 | Gripper |
| 27 | LeSlider, seven-pot sketch only |

The sketch streams CSV at **115200 baud**. It does not enable Wi-Fi; GPIO25,
26 and 27 use ADC2 on the classic ESP32. Software inversion handles a pot
whose direction is reversed.

**These are standalone pot-reader sketches.** Flashing one onto the current
COM7 WROOM replaces `wroom_io` and its IMU/haptic interface. Use a separate
pot-reader ESP32 to retain that interface. Close Arduino Serial Monitor before
starting a Python controller. COM9 in the examples is only a placeholder for
the pot-reader port, not a detected device.

## Test input before moving the arm

Run from the repo root:

```powershell
.\control.ps1 ports

# Six-pot sketch:
.\control.ps1 pots -LeaderPort COM9 -DryRun

# Seven-pot sketch:
.\control.ps1 leslider -LeaderPort COM9 -DryRun
```

Use only the command matching the uploaded sketch. Dry-run mode reads the
potentiometers and prints mapped targets without connecting to the arm. Press
**p** to print readings and targets, and **q** to quit.

The copied pot ranges are specific to your previous hardware. To learn ranges
for new wiring or new pots, add `-Recalibrate` to the matching dry-run command
and follow the console instructions. The seven-pot calibration captures ranges
between two presses of **e**; sweep each pot through its intended travel.
Saved files are in `leslider_control/config/`.

## Control the hardware

Stop `sensor.ps1 robot` or any other program using the robot's COM6 port first.
These are alternate controller sessions; the copied pot controllers do not yet
integrate the motor-load haptic dashboard. Each serial port has one owner.

```powershell
# Arm + gripper:
.\control.ps1 pots -LeaderPort COM9 -RobotPort COM6

# Arm + gripper + LeSlider on servo ID 7:
.\control.ps1 leslider -LeaderPort COM9 -RobotPort COM6
```

Both start with movement streaming paused. Live startup configures the servos
and disables torque, so support the arm before starting. Focus the terminal:

| Key | Action |
|---|---|
| e | Enable/pause potentiometer control |
| t | Disable motor torque |
| c | Recenter relative mapping |
| p | Print readings/targets |
| q or Esc | Quit and disable torque |

The seven-pot controller uses absolute mapping by default. The existing
phase configuration, travel limits, stale-input handling and slider session
reference checks are preserved. Follow the endpoint-reference prompts for the
physical rail after a power cycle; its multi-turn count is not persistent.
The copied slider calibration spans 6,604 encoder ticks, with ID 7 reversed.
Treat those endpoints as specific to this rail, not generic LeSlider limits.

For only the slider's position test window:

```powershell
.\control.ps1 slider -RobotPort COM6
```

It controls only ID 7, asks you to establish a physical endpoint reference,
and supports targets from 0 to 100. To recapture slider endpoints by hand:

```powershell
.\control.ps1 calibrate-slider -RobotPort COM6
```

That routine disables ID 7 torque and records endpoints; it preserves the six
arm calibration entries. Keep servo power connected while capturing both ends.
The copied `.slider_safety.json` metadata and its checks remain active.

## Advanced options and validation

```powershell
.\control.ps1 pots -Options
.\control.ps1 leslider -Options
.\control.ps1 slider -Options
.\control.ps1 test

# Example: invert chosen pots using the full controller CLI:
uv run --python 3.12 leslider_control/run.py leslider --leader-port COM9 --robot-port COM6 --dry-run --invert shoulder_pan,shoulder_lift,wrist_roll,slider
```

The existing 51 Python tests cover parsing, six/seven-channel mappings,
calibration, slider reference handling, and movement safeguards without opening
hardware. Both sketches are compiled for `esp32:esp32:esp32` as a build check;
compilation does not upload firmware or validate physical wiring.

## Files and provenance

- `scripts/`: six-pot bridge, seven-pot bridge, slider follower, ID-7 tester,
  calibration utility and their helper/compatibility modules.
- `arduino/`: standalone six-pot and seven-pot WROOM sketches.
- `config/`: saved pot ranges, inversions and elbow phase configuration.
- `calibration/robots/`: copied six/seven-axis motor calibration and slider
  safety metadata. Runtime calibration changes stay inside this repo.
- `../robot_control/vendor/lerobot/`: shared motor/robot driver and camera
  interface dependencies. The hardware-only type aliases omit PyTorch.
- `provenance.json`: source paths, hashes and adaptation notes. Original
  source files were not changed; the copied Apache 2.0 license is included.

The old `wh148_leslider_7dof_pot_calibration.json` file was not imported: it
assigns GPIO26 to the slider and GPIO27 to the gripper, which disagrees with
the current sketch. The included `wh148_slider_7dof_pot_calibration.json`
matches GPIO26 = gripper and GPIO27 = slider.
