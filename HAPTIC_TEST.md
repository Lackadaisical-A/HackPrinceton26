# IMU + vibration smoke test

**For the WROOM's DRV2605L I2C board, use [wroom_io](wroom_io/README.md)
and `.\sensor.ps1 motion-test -Port COM7`.** The GPIO18 instructions below
describe the older transistor-driver test, not the current I2C motor wiring.

These sketches target an active-HIGH driver input on GPIO18, one motor
output per ESP32. They do not drive a bare coin motor or an LRA directly.
No vibration occurs automatically on boot.

## Wiring

For a DC/ERM coin, use a 3.3V-logic-compatible motor driver or a suitably
rated logic-level N-channel MOSFET circuit. Connect its active-HIGH input
to GPIO18 and share GND with the ESP32. Use the motor's rated supply voltage.
For a discrete MOSFET circuit: motor+ to motor supply+, motor- to drain,
source to GND, gate through about 100 ohms to GPIO18, and a 10k gate-to-GND
pulldown. Put a flyback diode across the motor (cathode/stripe to motor+).
Select components and supply for the motor's startup/stall current.
An LRA coin instead needs a matching haptic driver and different firmware.

You can first check the output with an LED and a 1k resistor to GND,
without connecting the motor. Keep existing IMU wiring as it is.

## Upload in Arduino IDE

Stop bridges, viewers, test programs and Serial Monitor before uploading.
Confirm port identities with `.\sensor.ps1 ports` (ports can change).

| Device | Sketch to open | Board | Port |
|---|---|---|---|
| WROOM, one IMU | haptic_smoke_wroom/haptic_smoke_wroom.ino | ESP32 Dev Module (or exact board) | COM7 |
| S3, two IMUs | haptic_smoke_s3/haptic_smoke_s3.ino | ESP32S3 Dev Module (or exact board) | COM8 |

WROOM library: Adafruit BNO08x and its dependencies.
S3 library: SparkFun BNO080 Cortex Based IMU.
S3 memory: 16MB Flash, OPI PSRAM. COM8 was a CH343 USB-to-UART port:
use USB CDC On Boot Disabled for that connector. For the native USB
connector instead, enable CDC and select its corresponding COM port.
Click Upload for each device. IMU addresses on S3 remain 0x4A and 0x4B.

## Run the test

Close Serial Monitor. Test one board at a time:

```powershell
.\sensor.ps1 haptic-test -Port COM7 -Expected 1
.\sensor.ps1 haptic-test -Port COM8 -Expected 2
```

Each command waits for baseline readings, checks PING/PONG, requests one
500ms pulse, verifies ON/OFF acknowledgments, counts readings from each IMU
between ON and OFF, and sends STOP before exiting. Confirm the coin physically
vibrates and then stops. Software checks driver commands, not motor current.
If a sensor stops or the board resets when the motor starts, inspect power,
grounding, driver and diode wiring. These are functional smoke tests, not a
hard real-time timing guarantee: sensor library calls can briefly block.

For a manual test, Serial Monitor at 115200 baud with Newline can send:

```text
PING
VIBRATE,150
STOP
```

Pulse duration is limited to 1..500ms per command. Commands use nonblocking
serial parsing and millis-based timing. Startup and acknowledgments are text;
quaternion lines stay compatible with the Firebase bridge. Do not run the
bridge and smoke script on the same COM port at the same time. Existing
bridge scripts do not yet forward vibration commands while streaming.
