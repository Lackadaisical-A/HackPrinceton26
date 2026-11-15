# Two BNO085s and two coin motors on ESP32-S3

Install **SparkFun BNO080 Cortex Based IMU** in Arduino Library Manager.
Also install **Adafruit DRV2605 Library** and its dependencies.
This dual-sensor sketch uses that library because the Adafruit BNO08x driver
uses shared transport state and cannot simply be instantiated twice.

Both sensors share SDA GPIO8, SCL GPIO9, 3.3V and GND. Set one sensor's
I2C address to 0x4A and the other to 0x4B using the breakout's address
pin/jumper. On Adafruit-style breakouts DI low selects 0x4A, high selects
0x4B. Check the markings/documentation for your specific breakout.
Power-cycle after changing addresses. Duplicate addresses cannot be
reliably detected by an I2C scan.

Upload bno085_reader_s3.ino to COM8. Serial output is:

```text
imu1,1.00000,0.00000,0.00000,0.00000
imu2,1.00000,0.00000,0.00000,0.00000
```

Close Serial Monitor, then from the repo root:

```powershell
.\sensor.ps1 record -Port COM8 -Sensor s3
```

Firebase paths are /live/sensors/s3_imu1 and /live/sensors/s3_imu2.
Both streams go into one local session with sensor_id on each sample.

## Motor wiring and computer control

| Device | Connection | Address / control |
| --- | --- | --- |
| BNO085 imu1 | SDA GPIO8, SCL GPIO9 | 0x4A, AD0 low |
| BNO085 imu2 | SDA GPIO8, SCL GPIO9 | 0x4B, AD0 high |
| DRV2605L / motor 1 | Same SDA GPIO8, SCL GPIO9 | 0x5A; coin on OUT+/OUT- |
| Transistor / motor 2 | GPIO15 through base resistor | 20 kHz, 8-bit PWM |

Use common GND and 3.3 V I2C logic. The DRV2605L is configured for the same
DC/ERM coin motor as the WROOM, with continuous realtime drive. The second
motor uses the same working NPN circuit: emitter to GND, collector to motor
negative, motor positive to a suitable motor supply, GPIO15 through the base
resistor to base. Add a 10 kOhm base-to-GND pulldown and a diode across the motor
with its striped end toward motor positive. The Tatoko coin is nominally 3 V;
use a suitable 3-3.3 V supply sharing GND. Identify the transistor's actual
emitter/base/collector; do not infer its pin order from the package shape.
GPIO15 drives the transistor base, not the motor directly.

Upload **bno085_reader_s3.ino** to **COM8 / ESP32S3 Dev Module**, with **16MB
flash**, **OPI PSRAM**, and **USB CDC On Boot: Disabled** for your CH343 USB/UART
connection. Use Arduino-ESP32 **3.x**. Close dashboard/robot and Serial Monitor
before uploading or running these commands from the repository root:

```powershell
# DRV2605L coin: 1..127 active intensity (0 in the helper means stop).
.\sensor.ps1 motor -Port COM8 -Motor 1 -Strength 64 -Seconds 3

# Transistor coin on GPIO15: 0..255 PWM duty.
.\sensor.ps1 motor -Port COM8 -Motor 2 -Strength 128 -Seconds 3
```

Each command vibrates continuously for three seconds, reads IMU data at the
same time, then stops both motors. Ctrl+C also requests STOP. The helper sends
overlapping 500 ms commands; if the computer stops sending, the last command
expires during normal firmware operation. Low PWM levels may not start the
coin, and duty/intensity is not a calibrated force measurement. No motor runs
automatically at startup. PWM is initialized off before sensor setup.

The firmware accepts newline-terminated commands at 115200 baud:

| Command | Result |
| --- | --- |
| STATUS | `# DEVICE S3_IO_V1 IMU1=1 IMU2=1 DRV2605L=1 MOTOR=ERM PWM2=1 PWM_PIN=15` when initialized |
| PING | `# PONG` |
| VIBRATE,500,64 | DRV2605L motor; 1..500 ms, strength 1..127 (default 64) |
| VIBRATE2,500,128 | Transistor motor; 1..500 ms, duty 0..255 |
| STOP1 | Stop DRV2605L only |
| STOP2 | Stop transistor motor only |
| STOP | Stop both |

The two timers are independent, so a single host connection can command both
motors concurrently. Repeated commands renew an output without an off gap.
Status flags indicate initialization, not physical motor detection; actual IMU
freshness comes from its sample stream. Reset/recovery of either IMU requests
both motors off and aborts an active run of the manual helper. A failed I2C bus
can also prevent commands reaching the DRV2605L, so firmware acknowledgments
cannot guarantee its physical output state during a bus fault.

These are the Arduino/manual-control interfaces. Existing dashboard and robot
automatic vibration still targets COM7. The standalone helper must own COM8;
do not run it alongside another program holding that port. Restart the
dashboard after testing to visualize all three IMUs and publish to Firebase.

## Recovery from disappearing sensors

The sketch detects two seconds without quaternion reports and attempts to
reinitialize that sensor. Missing sensors are retried every five seconds.
It also re-enables reports after a sensor reset. Diagnostic lines start with
`#`, so the existing dashboard and recorder ignore them as sensor samples.
`initialized` means the handshake succeeded; `streaming` confirms actual data.
The bus stays at 100 kHz with a 100 ms transaction timeout. Reinitialization is
blocking and can briefly interrupt both streams. This is an attempted recovery,
not a guarantee against a hardware fault or a stuck I2C bus.
Before any reset/reinitialization, the sketch requests both motors off. Reset
notifications use `# IMU RESET ...`; retry notifications use `# IMU RECOVERY ...`.

To install: stop the dashboard/robot process and close Serial Monitor, then
upload this sketch to **COM8**, **ESP32S3 Dev Module**, **16MB flash**, **OPI
PSRAM**. Use **USB CDC On Boot: Disabled** for the CH343 USB/UART connector
currently providing COM8 (Enabled is for the native USB connector). Restart
`.\sensor.ps1 dashboard` from the repository root afterward.

Unplugging USB resets the sensors' power as well as the ESP32. If that is the
only way to recover, add a reset wire so firmware can reset a locked-up sensor:

| Sensor | RST connection | Optional INT connection |
| --- | --- | --- |
| imu1 / 0x4A | S3 GPIO6 | S3 GPIO4 |
| imu2 / 0x4B | S3 GPIO7 | S3 GPIO5 |

Disconnect USB before changing wiring. Use these GPIOs only if they are free on
your board. Keep each RST and INT separate; keep the existing shared SDA GPIO8,
SCL GPIO9, 3.3V and GND. RST must have a pull-up to 3.3V on the sensor side
(normally fitted on the breakout; add 10 kOhm if yours has none).

After wiring both RST pins, change `resetPins` from `{-1, -1}` to `{6, 7}` and
upload again. If you only wire imu1 RST, use `{6, -1}`. The sketch pulses only
the sensor being initialized. Optional INT connections reduce polling when no
data is ready: change `interruptPins` to `{4, 5}` only after connecting both
wires, or `{4, 255}` for imu1 only. Both features are disabled by default so
the existing wiring still works.

Check the 0x4A sensor's AD0 connection to GND, power/ground, SDA/SCL solder
joints and jumper contacts. Keep sensor wires short and away from motor leads.
With USB disconnected, swap the two sensors' AD0 settings, power up, and see
whether the failure follows the physical sensor or stays at 0x4A; this separates
sensor/wiring problems from address-specific behavior. Do not leave duplicate
addresses. Re-test with the robot stationary. After a sensor reset or address
swap, verify role mapping and capture neutral again before resuming arm control:
the game rotation vector's reference can change after a reset.

Adafruit documents [BNO085 I2C timing problems with ESP32/S3](https://learn.adafruit.com/i2c-addresses/troublesome-chips)
and the [active-low RST and INT signals](https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/pinouts).
Reset/retry can help recover; it does not fix wiring, power or all protocol
timing failures. Repeated failures after those checks warrant moving to SPI.

Connect the WROOM with a second data USB cable, run `.\sensor.ps1 ports`,
then in a separate terminal (replace COM7 with the actual new port):

```powershell
.\sensor.ps1 record -Port COM7 -Sensor wroom
```

Its existing single-sensor sketch remains compatible. Its Firebase path is
/live/sensors/wroom. Each board records a separate session; arrival timestamps
are not hardware synchronization. The existing USB viewer currently expects
the single-sensor format, not the S3's tagged two-sensor output.
