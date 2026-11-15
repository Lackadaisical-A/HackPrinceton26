# Computer-controlled hand IMU, joystick and two coin motors on COM7

One firmware upload gives Python a stable serial interface. The firmware
streams orientation and performs requested motor writes. All motion thresholds,
gesture logic and decisions about when to vibrate live in `motion_haptics.py`.
Pulse expiration stays on the board so a lost host connection cannot leave a
requested pulse running indefinitely during normal firmware operation.

## One-time setup

Open `wroom_io.ino` in Arduino IDE. Select **ESP32 Dev Module** (or your exact
WROOM board) and **COM7**. Install **Adafruit BNO08x** and **Adafruit DRV2605
Library**, including dependencies, and upload. Close Serial Monitor afterward.
The earlier `haptic_smoke_wroom` sketch only toggles GPIO18 and cannot control
this I2C driver. COM8/S3 is not involved in this test.

Both BNO085 and DRV2605L use SDA -> GPIO21 and SCL -> GPIO22, common GND,
and 3.3V-compatible supply/logic per the breakout specifications. Coin leads
connect to the DRV2605L motor OUT+/OUT- terminals. No GPIO18 connection is
used. Addresses are BNO085 0x4A/0x4B and DRV2605L 0x5A.

This initial configuration assumes a **DC/ERM coin** using the library's
open-loop mode. The driver model alone does not identify the actuator type.
An LRA requires its own configuration. Use a supply suitable for your motor;
strength is an RTP drive level, not a calibrated voltage or vibration force.

## Hand joystick for the robot claw

The joystick is powered from **3.3 V**, with GND shared with the WROOM:

| Joystick signal | WROOM pin |
| --- | --- |
| VRx | D2 / GPIO2 |
| VRy | D4 / GPIO4 |
| SW | D5 / GPIO5 (internal pull-up; pressed is low) |

This matches the requested wiring. Do not power the joystick from 5 V because
its analog outputs connect directly to ESP32 inputs. GPIO2 and GPIO4 are ADC2
inputs; this USB-only sketch leaves WiFi off. The computer handles Firebase.
The sketch samples both axes at about 50 Hz, averages four ADC readings,
debounces the button for 30 ms, and sends a separate packet:

```text
# JOYSTICK,12345,2048,2050,0
```

Fields are board uptime in milliseconds, raw X (0..4095), raw Y (0..4095), and
pressed (0/1). The existing `w,x,y,z` IMU lines and both motor protocols remain
available. `STATUS` adds `JOYSTICK=1 X_PIN=2 Y_PIN=4 SW_PIN=5`; this describes
firmware support, not a physical joystick-presence detector.

**GPIO2 affects flashing:** it must be low or disconnected for the ESP32 serial
bootloader. If an upload fails, disconnect USB, disconnect VRx from D2, upload
with VRx disconnected, then disconnect USB again before reconnecting VRx.
Normal boot ignores GPIO2's level. If you later want to avoid this upload
workaround and ADC2/WiFi limitations, move the axes to available GPIO32/33 and
update the pin constants and status description together.
See [Espressif boot mode requirements](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html)
and [ADC2 limitations](https://docs.espressif.com/projects/esp-idf/en/v4.3/esp32/api-reference/peripherals/adc.html).

Upload this updated sketch once to COM7. Then close Serial Monitor and run
`.\sensor.ps1 dashboard`: verify the joystick X/Y numbers change and the button
reads PRESSED when clicked. Close that dashboard before starting robot mode:

```powershell
.\sensor.ps1 robot -RobotPort COM6
```

Release the joystick to center, then click **Enable joystick claw (center
stick)**. By default Y changes opening/closing speed; centered holds the pose,
and pressing SW pauses the claw until re-enabled in the window. X is displayed
but unused by default. Use `-JoystickAxis x` or `-ReverseClaw` to change mapping
without reflashing. The firmware only reports inputs; robot decisions run on
the PC. See [robot control setup](../robot_control/README.md#joystick-claw-control).

## Second motor: transistor and GPIO15 PWM

The added output is for a **DC/ERM coin motor**, not an LRA. It is independent
of the DRV2605L, uses no I2C address, and supports 8-bit PWM intensity from
0 (off) to 255 (full duty). The carrier is 20 kHz. Intensity is not linear or
calibrated: low duty may not start the motor. Supply voltage must be suitable
for the motor even at full duty; PWM does not make an excessive supply safe.

Correct the wiring before powering/testing. A motor connected between the
transistor and GND, with the emitter also at GND, has no intended power source.
Use this circuit, identifying pins by their actual datasheet:

```text
Motor-rated positive supply ---- motor (+)
motor (-) ---------------------- transistor COLLECTOR
transistor EMITTER -------------- GND
ESP32 D15/GPIO15 -- base resistor -- transistor BASE
transistor BASE -- 10 kOhm ------ GND
motor supply negative ---------- ESP32 GND
```

Connect the flyback diode **across the motor**, not in series: striped/cathode
end to motor (+), unstriped/anode end to motor (-)/collector. Do not connect
this motor to the first motor's DRV2605L outputs. Never use the GPIO as the
motor's power supply. Verify the base resistor against motor startup/stall
current and GPIO drive limits; its value and the motor rating must be known
before confirming this circuit. A suitable separate motor supply sharing GND
can keep motor startup noise off the IMU power rail.

Reported parts for this build: a 1 kOhm base resistor, transistor marked
`2N222A / 1726` (manufacturer/pinout unconfirmed), and Tatoko 20PCS 10mm x 3mm
coin motors. The [matching Tatoko listing](https://www.amazon.com/dp/B07Q1ZV4MJ)
specifies 3 V, 85 mA and a 1.5-3.7 V operating range. Confirm the package matches
before using those ratings. Use a suitable regulated 3-3.3 V motor supply,
not 5 V. The board regulator must have enough spare current if sharing its
3.3 V rail; the listed 85 mA is not a startup/stall-current specification.
With approximately 0.8 V base-emitter drop, the 1 kOhm resistor supplies only
about (3.3-0.8)/1000 = 2.5 mA. That may run the motor but does not establish
that the transistor saturates at startup. Verify the actual transistor and
motor startup current before selecting a smaller base resistor or a suitable
3.3 V logic-level MOSFET.

Do not assume flat-face-left is emitter. For example, onsemi **P2N2222A** is
collector/base/emitter, and other 2222-family parts differ. Confirm the exact
manufacturer/package and its viewing direction before connecting power.

GPIO15 is a boot strapping pin with a default pull-up. The base pulldown helps
keep the switch off while the pin floats, but a startup twitch is still possible.
If GPIO18 is free, it avoids that strapping-pin issue: move the resistor lead
to GPIO18 and change `PWM_MOTOR_PIN` to `18` in the sketch. The current default
is `15` to match the requested wiring. Firmware sets duty zero before starting
I2C or initializing either sensor/driver.

After checking the circuit, upload **wroom_io.ino** to **COM7 / ESP32 Dev Module**
using **Arduino-ESP32 3.x**. This is one firmware upload; subsequent intensity
and timing changes come from the computer. Nothing needs uploading to COM8.

Close dashboard, robot, and Serial Monitor before using these commands:

```powershell
# Second/transistor motor: ~50% duty continuously for 3 seconds, then stop.
.\sensor.ps1 motor -Port COM7 -Motor 2 -Strength 128 -Seconds 3

# Try a stronger level after confirming the motor supply/current ratings.
.\sensor.ps1 motor -Port COM7 -Motor 2 -Strength 192 -Seconds 3

# First/DRV2605L motor: its original 0..127 scale.
.\sensor.ps1 motor -Port COM7 -Motor 1 -Strength 64 -Seconds 3
```

`Ctrl+C` stops both outputs. The helper refreshes 500 ms commands every 100 ms,
so vibration is continuous while the command runs. It reads the IMU stream
at the same time, checks motor acknowledgments, and prints a sample count.
Missing acknowledgments or an IMU reset stop the run. A lost connection lets
the last command expire within 500 ms during normal firmware operation.
Acknowledgments confirm firmware commands, not physical motor current or
vibration. This manual test can run even when the IMU is absent; a sample count
of zero means IMU streaming was not verified.

The helper owns COM7 and stops both motors at startup and shutdown. For an
application that already owns COM7, send the serial commands below through
that same connection. Existing dashboard/robot feedback targets the DRV2605L;
it does not automatically mirror onto the transistor motor.

Sources: [transistor motor wiring and diode](https://learn.adafruit.com/adafruit-arduino-lesson-13-dc-motors/breadboard-layout),
[P2N2222A pinout](https://www.onsemi.com/pdf/datasheet/p2n2222a-d.pdf),
[GPIO15 boot behavior](https://docs.espressif.com/projects/arduino-esp32/en/latest/boards/ESP32-DevKitC-1.html),
[ESP32 PWM API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html).

## Motion test (on your computer)

Stop other programs using COM7, then run from the repository root:

```powershell
.\sensor.ps1 motion-test -Port COM7
```

Hold still in your desired neutral pose for one second when prompted.
Python saves that orientation. Vibration then stays on while the IMU is
tilted/rotated away from neutral, even when held stationary at that angle.
Greater angular difference produces greater strength. Within 5 degrees of
neutral the motor is off; activation has 1 degree of hysteresis to avoid
chatter. Strength ramps from a minimum drive of 30 to the selected maximum
(64 by default) at 45 degrees. The minimum helps a coin motor start spinning.
A pure translation with unchanged orientation is not detected.

Change behavior without reflashing:

```powershell
.\sensor.ps1 motion-test -Port COM7 -Threshold 5 -FullScale 45 -Strength 80
```

`-Threshold` sets the off zone, `-FullScale` the angle for maximum vibration,
`-Strength` the maximum drive, and `-MinStrength` the minimum active drive.
Restart the command to capture a new neutral pose. The BNO085 game rotation
vector's heading can drift over time; recapture neutral when necessary.

The computer refreshes a 300ms motor command every 100ms while receiving
fresh IMU samples. These overlapping commands keep vibration continuous.
Returning to neutral sends STOP immediately. Ctrl+C also requests STOP;
a lost host connection lets the firmware's last command expire. A stale
IMU stream or missing acknowledgment stops the program. No firmware upload
is needed if `wroom_io.ino` is already installed.

The previous gesture-pulse behavior is available with:

```powershell
.\sensor.ps1 motion-test -Port COM7 -Mode gesture -Threshold 8 -PulseMs 200
```

This local test does not publish to Firebase;
the serial port has one owner. Run the existing live/record commands after
stopping this test, or later combine their work into one host process.

## Serial protocol (115200 baud, newline)

| Host command | Result |
|---|---|
| STATUS | Firmware identity and IMU/DRV detection status |
| PING | `# PONG` |
| VIBRATE,200,64 | Timed RTP pulse; duration 1..500ms, strength 1..127 |
| VIBRATE,200 | Same with default strength 64 |
| VIBRATE2,500,128 | Second motor: GPIO15 PWM; duration 1..500 ms, duty 0..255 |
| STOP1 | Stop only the DRV2605L motor |
| STOP2 | Stop only the GPIO15 motor |
| STOP | Stop both motors |

Each motor has an independent expiry timer. Repeating a command before it
expires renews that output without inserting an off interval. `STATUS` adds
`PWM2=1 PWM_PIN=15` when PWM initializes; this does not detect a connected motor.
Second-motor acknowledgments are `# HAPTIC2 ON <ms> <duty>`, `# HAPTIC2 OFF`,
and `# HAPTIC2 STOPPED`. An IMU reset stops both outputs.

IMU output stays `w,x,y,z`, compatible with existing bridge/viewer tools.
Acknowledgments begin with `#`. The motion test requires WROOM_IO_V1 firmware
and both devices; it stops on missing acknowledgments or a stale IMU stream.
DRV register readback confirms a command, not physical motor movement.

The DRV implementation follows the [Adafruit RTP example](https://github.com/adafruit/Adafruit_DRV2605_Library/blob/master/examples/realtime/realtime.ino)
and [TI DRV2605L signed RTP documentation](https://www.ti.com/lit/ds/symlink/drv2605l.pdf).
