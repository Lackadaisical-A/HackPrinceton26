# Wearable test bench

Run from the repo root, after closing the old dashboard, robot controller, and Arduino Serial Monitor (each COM port has one owner):

```powershell
.\sensor.ps1 wearable-test
```

Uses the existing firmware: `wroom_io/wroom_io.ino` on COM7 and `bno085_reader_s3/bno085_reader_s3.ino` on COM8. No new Arduino upload is needed if both boards already have these versions. This is a local test app: it does not connect to the SO-101 or Firebase.

To attach the physical SO-101 to this same calibrated mapping, use
`.\sensor.ps1 wearable-robot -RobotPort COM6`. That mode adds a separate physical
control window and connects read-only until manual enable. See
[physical control instructions](../robot_control/WEARABLE.md).

```powershell
# Different port assignments
.\sensor.ps1 wearable-test -Port COM7 -S3Port COM8

# Synthetic demonstration, without opening serial ports
.\sensor.ps1 wearable-test -Demo

# Six-second health check; sends STATUS and STOP, never starts a motor
uv run wearable_test/run.py --check

# Tests without hardware
uv run --python 3.12 --with pybullet==3.2.7 --with pillow python -m unittest wearable_test.test_wearable wearable_test.test_robot_preview wearable_test.test_retarget wearable_test.test_render_transport wearable_test.test_recovery -v
```

## Sensor layout

| Body segment | Board / sensor | Displayed mounting point |
| --- | --- | --- |
| Hand | WROOM / COM7 | Hand center, halfway from wrist to fingertips |
| Bicep | S3 / COM8 / imu1 / 0x4A | 25% from elbow toward shoulder |
| Forearm | S3 / COM8 / imu2 / 0x4B | 33.3% from wrist toward elbow |

Default lengths are shoulder–elbow **30 cm**, elbow–wrist **26 cm**, wrist–fingertips **18 cm**. Click **Dimensions / sensor axes** to enter your own measurements and adjust the mounting points. Settings save to ignored `wearable_test/settings.local.json`.

The 3D view derives joint and sensor positions from the IMUs' orientations and these lengths. The shoulder is fixed. This is a connected arm visualization, not measured XYZ tracking or a clinically validated joint-angle measurement. Markers lie on the limb centerline; skin thickness and strap offsets are omitted. The displayed wrist bend is the angle between forearm and hand directions, not wrist roll.

## Calibration (no board-axis knowledge required)

Keep all sensors strapped firmly in place and stand or sit with your torso still. Leave each axis set to **Auto**.

1. Click **Capture A**. During the three-second countdown, extend the instrumented arm **straight forward at shoulder height** (90° from your torso), palm facing the floor. Keep the elbow and wrist straight. Hold still for the following two-second capture.
2. Lower the straight arm in the same forward/down plane toward your side, pointing your fingers toward the floor. Keep the elbow and wrist straight and avoid twisting the arm. Click **Capture B**, get into position during the countdown, and hold still for the two-second capture. A roughly 90° lowering motion is ideal.
3. Bend the elbow, move the shoulder, and bend/roll the wrist. The three connected segments should follow. Drag the view to orbit; scroll to zoom.

The app estimates each sensor's mounting direction from gravity in pose A and the lowering rotation between A and B. This is our geometric calibration method; consistent movement between poses is essential. It rejects too few samples, large motion during capture, stale data, and strongly twisted or insufficient lowering motions. Repeat A/B if a strap moves or mounting is wrong. Use **Recenter arm** after a board reset or heading drift when straps have not moved. The game rotation vector uses gyro/accelerometer fusion without a magnetic heading reference; see [Adafruit's report descriptions](https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/report-types).

**Manual alternative:** in **Dimensions / sensor axes**, select the marked board axis pointing toward your fingertips in pose A, separately for each sensor (`+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`). If all three have manual axes, **Capture A alone** completes calibration. If any axis remains Auto, capture B too. Save settings before capturing.

Calibration samples need all three streams fresh and approximately synchronized. A serial reconnect or explicit board/IMU restart invalidates the world reference while preserving the learned sensor mounting. A delayed status packet or timing gap alone preserves both. If a stream becomes stale, the last calibrated pose is held and clearly labeled stale; joystick packets do not keep an IMU falsely marked live.

After a reset, click **Recenter arm · pose A only**: extend straight forward at
shoulder height, palm down, with elbow and wrist straight. The same three-second
countdown and two-second stable capture restores the reference. The enabled robot
preview resumes automatically; an intentional manual Pause remains paused.
There is no need to repeat pose B unless a strap or sensor mounting moved.

Successful calibration saves only the mounting axes and arm settings to ignored
`calibration.local.json`. On a future app launch, use Recenter arm once. Old world
orientations are deliberately not restored across board restarts. Saved mounting
is rejected if arm settings differ or its axes are invalid. Demo mode neither
loads nor overwrites the saved hardware mounting.

## SO-101 URDF add-on (after calibration)

After completing A/B, click **Enable SO-101 URDF preview**. A second view inside
the same application renders the actual local SO-101 URDF and meshes. It uses
the live sensor readings already owned by Wearable Lab, with no second serial
connection or Firebase dependency. Closing Wearable Lab also closes the preview.

- The straight-forward calibration pose maps to a straight-forward robot arm, using offsets derived from the actual URDF. Its zero joint angles describe a folded pose, so zero angles cannot serve as calibration neutral.
- Shoulder pan follows shoulder-to-wrist reach direction, using both the bicep and forearm. Moving the forearm left/right turns the base even when the bicep hangs down. The mounting foot remains fixed; the shoulder pan joint rotates above it.
- Bicep and forearm directions are scaled to the robot's link lengths. A two-link inverse-kinematics solution drives shoulder lift and elbow to track that wrist reach. It keeps the elbow bend on the branch with the wider URDF range, preventing sudden branch flips.
- Hand direction drives wrist flex; palm orientation drives wrist roll. The robot has fewer degrees of freedom and different limits than a human arm, so its elbow shape and some wrist poses will differ. Unreachable targets are bounded and reported as **At URDF limit**, rather than silently looking stuck.
- Joystick **Y=0 -> URDF gripper maximum/open**, **Y=4095 -> minimum/closed**,
  and neutral **Y=1810 -> halfway open**. This is position control. Returning the
  stick to neutral returns the claw toward halfway. The simulated gripper uses
  the actual URDF range (-0.174533..1.74533 radians), not arbitrary screen angles.

Mounting calibration is applied before robot mapping. The preview uses a 35 ms
smoothing time constant (about 95% of a step in 105 ms, plus sensor/render delay),
with no slow simulated joint-speed cap. The physical robot controller's speed
limits are unchanged. The two views start from matching viewing directions.
Leave **Reverse direction** unchecked initially; use a checkbox only for a
deliberate per-joint reversal about the robot's calibration pose.
Drag to orbit, scroll to zoom, and use **Pause/Resume** to hold/restart the preview.
**Esc** in the preview pauses it and stops the wearable motors too.

Stale/out-of-sync IMU readings hold the pose and automatically resume when fresh,
including gaps longer than two seconds. An actual board/IMU reset holds until
**Recenter arm** completes. Starting a new mounting calibration requires A/B.
A stale joystick or pressed switch
holds only the simulated claw. This add-on never connects to a physical SO-101.
The separate `sensor.ps1 robot` controller uses the same absolute claw mapping,
with its existing manual-enable, speed, load and position checks.

Rendering and its process-pipe communication run outside Tk's UI thread. Only
the latest requested pose is kept, so slow rendering cannot build a queue of old
motion. The preview displays renderer FPS and frame age, retries a stalled
renderer, and offers **Restart renderer** after repeated failures. Serial writes
also run outside the UI's snapshot lock. Restart Wearable Lab after updating these
Python files; an already-open window retains the old code. No firmware upload is
needed for these preview changes.

**FPS can change while the robot is held:** it measures rendering, not new arm
poses. The preview separately displays pose age or **Pose HELD**, with a large
overlay explaining the hold and the affected board/reset message. Exceptions in
the input callback are logged and retried rather than silently ending pose
updates. `wearable_test/runtime.log` records reset reasons and pose-state changes
(rotating local log, ignored by Git). Repeated real sensor resets still require
diagnosing the board, power, or connection; recentering repairs the reference,
not the underlying hardware fault. Motor outputs never restart automatically.

**View quality** defaults to Fast (up to 320×200), with Balanced (480×300) and
Detailed (640×400) available. The bundled display meshes remove small CAD details
to reduce rendering cost; the original model stays in `so101_sim/model`. Joint
transforms and limits are identical. The display copy omits collision geometry and
is only for visual preview. Rebuild it after editing source meshes or the URDF:

```powershell
uv run wearable_test/build_preview_model.py --benchmark
```

The builder records source hashes and mesh counts in `model_preview/manifest.json`
and preserves the source license. If the source URDF changes or display assets
are missing, the preview falls back to the full model until rebuilt.

## Test the joystick and all four coins

- Move the hand joystick through its full range. Raw X/Y readings still show ADC values 0..4095; the dot and signed outputs use center **X=1360 / Y=1810** and a **15% dead zone**. Released at those readings, the dot is centered and outputs are zero. Edit `bno085_reader/joystick_calibration.json` and restart to adjust. Press/release and check **SW PRESSED/released**. The URDF claw uses this same calibration; the main wearable view does not command a physical claw.
- Four motor panels represent **WROOM DRV2605L**, **WROOM GPIO15 transistor**, **S3 DRV2605L**, and **S3 GPIO15 transistor**.
- Start at 40%. Click **Test 1s** to identify each coin. Then click **Run 10s** to check sustained vibration while moving your arm and stick. Adjust intensity during the run. You can start multiple panels to test them together.
- Each Run is bounded to ten seconds. **Stop** affects that panel; **Stop all motors**, **Esc**, or closing the window stops both boards. A 0% slider stops that output. Calibration also stops all motors.
- The host refreshes 500 ms firmware commands every 100 ms after acknowledgment, giving continuous output rather than intentional gaps. Loss of acknowledgments, firmware errors or IMU reset/recovery stops the test. The existing firmware also expires an unrenewed command. These software timeouts are not a physical emergency-stop circuit.
- Driver intensity maps 0–100% to RTP 0–127; transistor intensity maps to PWM 0–255. Perceived strength is nonlinear, and a coin may not start at very low duty. Firmware ACKs confirm commands, **not that the motor physically vibrated**. Check each coin by touch.

Expected smoke-test result: three IMU rows stay LIVE (about 50 Hz on hardware), joystick X/Y and SW respond, each selected coin runs/stops, and moving the arm updates the 3D model without resets. Readings can continue during motor tests.

## Missing components

- **COM8 not present:** reconnect the S3 USB cable and check `.\sensor.ps1 ports`. The app retries every two seconds. If Windows assigns another port, restart with `-S3Port COMx`.
- **Permission/access denied:** close the other process holding that port (dashboard, robot, bridge serial reader, Arduino Serial Monitor). Reflashing is not needed just to release a port.
- **DRV2605L not ready, old firmware:** `DRV2605L=0` alone can mean either failed startup initialization or no current I2C acknowledgment. It does not prove the driver is physically missing. Earlier firmware initialized only once and could remain unavailable after a transient startup failure.
- **Updated haptic firmware:** upload the current `wroom_io/wroom_io.ino` to COM7 and `bno085_reader_s3/bno085_reader_s3.ino` to COM8 to get idle retries every three seconds and separate `DRV_INIT`, `DRV_ACK`, `DRV_ERR`, `DRV_REASON`, and `HAPTIC_FW=2` status fields. Recovery starts with motor output zero; it does not resume vibration automatically. Firmware verifies RTP/mode/ERM configuration with checked register reads.
- **No I2C response at 0x5A:** this is now a fresh bus check, independent of the startup result. With power disconnected, check shared ground, supply, SDA and SCL (WROOM: 21/22; S3: 8/9). This is separate from COM permissions. **I2C responds; init not ready** means the address responds but configuration has not completed; the app displays the reason. `DRV_ERR` is the Wire transaction error (0 = address acknowledged; 6 is our register-read-short diagnostic).
- **PWM ready** confirms the GPIO output initialized; the transistor/motor has no presence detector.
- **One S3 IMU stale:** inspect that sensor's power, SDA/SCL, AD0 strap and solder joints. An address strap must be stable (0x4A ground, 0x4B 3.3 V). Recovery can clear calibration; inspect the connection before recalibrating.

The app never starts motors automatically after a reconnect. Its port threads send STOP on opening/closing; a new manual button press is required to run a test.
