# Wearable Lab → physical SO-101

## Browser control: the Setup page

Everything in Wearable Lab's desktop windows also works from the web console,
and both stay in step because they drive the same Wearable Lab process. The
**Setup** page (key 4) follows the desktop order:

1. **Sensors**: IMU rate and age, board and DRV2605L status, the hand joystick,
   and coin bench tests (level, Test 1 s, Run 10 s, Stop).
2. **Dimensions**: arm lengths, sensor positions and axes (the desktop
   *Dimensions / sensor axes* dialog). Saving clears calibration.
3. **Calibrate**: A/B, recenter, elbow/shoulder/pan ranges, and the 3-IMU arm view.
4. **Verify**: a translucent ghost on the 3D arm shows the preview pose (where
   Align & follow will move), and per-joint **Reverse direction** switches
   (formerly only in the URDF preview window).
5. **Tune**: the five feel presets, Advanced speed/smoothing/rate/lead, and
   speed & load stops.
6. **Run**: **Align & follow** (or *Enable arm · relative*; this also enables the
   claw and haptics by default), **Enable claw** (to re-enable after a claw pause),
   **STOP / hold**, status and the joint table. The Console shows the same
   **Robot control** panel.

An enable from the browser is only a request: the serial worker applies the same
checks as the desktop button (fresh wearable input, calibration, load, saved
range) and the panel shows its answer. **Esc** anywhere in the console, the top
bar's **STOP / hold**, desktop Esc and a long joystick press all hold position
with torque on. Stop needs no matching session, so it works even if the
console's status is stale. Closing the browser does not stop the arm; the
desktop UI tick remains the safety heartbeat. Wearable Lab now serves the
browser without a robot port too (`.\sensor.ps1 wearable-test [-Demo]`), for
calibration and coin tests on the sensor bench.

## Integrated web console

The console's **Arm-to-robot calibration** panel captures A/B and recenters
through the desktop's existing sampling, stability checks and saved mounting.
It shows all three sensors and confirmed progress. Capture pauses arm/claw and
haptics. **Cancel / hold arm** also cancels capture; desktop Stop/Esc does the
same. Motion remains paused afterward; verify the preview and enable separately
with **Align & follow** (browser or desktop). Restart Wearable Lab and the bridge after updating
to load the calibration handler and port-5562 status publisher.

Run `.\demo.ps1 -Controller imu` for Wearable Lab on COM7/COM8, the physical arm
on COM6, vision, the WebSocket bridge, and the live web console. Optional
`-WroomPort`, `-S3Port`, `-RobotPort`, and `-CameraSource` select other devices.
Close an existing Wearable Lab first so only one process owns the serial ports.
The console shows measured joint pose and load at 10 Hz, using the same saved
URDF mapping as physical following. Publishing reuses the controller's reads;
it neither opens a second servo connection nor enables motion.

Recenter pose A, then use **Align & follow URDF** (claw and haptics come on with it) in the
physical panel as described below, or the browser's **Robot control** panel.
Browser target selection and vision guidance do not enable motors. A standalone wearable run can publish the same feedback
with `uv run wearable_test/run.py --robot-port COM6 --telemetry`.

## Guidance and resistance vibrations

Use **Enable guidance + resistance** in Wearable Lab, or the web console's
**Haptics** switch. This enables vibration only, never robot motion. Feedback
starts off and is turned on automatically by a successful arm enable (Align &
follow / Enable arm). After Stop, recenter, a reset or a fault it is off again
until the next arm enable or a manual switch-on.

| Position | Serial board | Output | Directional cue |
| --- | --- | --- | --- |
| Palm left (bottom left) | WROOM / COM7 | 1: DRV2605L | Left / down |
| Palm right (bottom right) | WROOM / COM7 | 2: transistor | Right / down |
| Forearm left (top left) | S3 / COM8 | 2: transistor | Left / up |
| Forearm right (top right) | S3 / COM8 | 1: DRV2605L | Right / up |

**Move toward the taps:** left/right lights that side, up taps both forearm
coins, down taps both palm coins; diagonals favor the corresponding corner.
Guidance uses 180 ms taps every 650 ms, up to 45%, with a 0.12 image deadzone.
It follows the target offset from the crosshair in the camera image. Mount and
orient the wrist camera consistently with the gripper before using these cues
to guide physical motion. Losing vision for 500 ms removes directional cues.

**Steady vibration means resistance:** palm intensity follows absolute gripper
load; forearm intensity follows the largest absolute arm-joint load. Deadbands
are 25 and 90 raw load counts; intensity rises from 28% to 45%, reaching the top
at 250 and 500 counts respectively. Guidance adds taps on top, capped at 65%.
Servo effort includes weight and motion; it is not calibrated force or proof
of object contact. Defaults are in `robot_control/haptics.py`.

The console shows the four commanded outputs in the actual 2x2 arrangement and
firmware acknowledgment status. An ACK is not a physical vibration measurement.
Pressure pads remain separate: no pad readings are invented from servo load.

Esc/Stop, holding the joystick switch for 0.7s, recenter, UI stalls, stale IMUs,
lost motor ACKs and board resets disarm feedback. The serial owners renew a
200 ms feedback lease and only refresh 500 ms firmware pulses after an ACK.
Neither network work nor vibration serial I/O runs on the arm-control thread.
Manual coin tests pause automatic feedback. Their labels use the mapping above.
The `haptics` publisher runs independently on port 5561, so the console can show
feedback status even with robot power off. Stale robot readings remove resistance
immediately; fresh camera guidance can still be enabled for a wearable-only demo.
An actual robot-controller fault stops feedback; explicitly re-enable for guidance
alone. The display labels resistance unavailable until fresh robot data returns.

Verification (close Wearable Lab before the hardware check):

```powershell
uv run robot_control/test_haptics.py  # simulated buses; no hardware
uv run robot_control/check_haptics.py # four short 35% identification pulses; no arm connection
```

```powershell
# Close the currently running Wearable Lab/dashboard/robot app first.
.\sensor.ps1 robot-check -RobotPort COM6
.\sensor.ps1 wearable-robot -RobotPort COM6
```

This opens the existing Wearable Lab plus **Physical SO-101 — COM6** controls.
COM7 owns the hand IMU and joystick; COM8 owns bicep/forearm IMUs. A separate
worker owns COM6. The current Arduino sketches already provide the required
data; no firmware upload is needed. This mode is local and does not publish to
Firebase. Demo input is rejected in physical mode.

## First movement

1. Keep the robot workspace clear and its power switch accessible. Connecting
   only reads motor telemetry; it does not enable torque or move any joint.
2. In Wearable Lab, use **Recenter arm · pose A** if mounting is saved: arm
   straight forward at shoulder height, palm down. Otherwise capture A and B.
3. Open the URDF preview and verify its directions. Hold your wearable arm still
   in the pose you want the robot to match. The physical panel shows the requested
   raw positions even before enabling; no position commands are sent at this stage.
4. Clear the robot's path and click **Align & follow URDF**. The first write latches
   its measured positions. Subsequent commands move gradually toward the captured
   preview pose at **90°/s**, subject to saved joint bounds and stop protections.
   Keep your wearable arm still until the panel says **LIVE calibrated URDF
   following**, then make a small movement to verify physical direction. Live
   following starts at **240°/s**, adjustable in **Live tuning...**. Alignment is joint interpolation, not
   collision-checked path planning; Stop/Esc holds it at any stage.
   Completion allows **4°** of actual motor error, sustained for **0.4 seconds**.
   The original 2° requirement kept alignment frozen at the observed shoulder
   3.4° / elbow 2.5° steady errors. A joint that fails to make progress for **6
   seconds**, or alignment lasting **30 seconds**, now pauses with the joint and
   remaining error instead of waiting indefinitely. The panel shows progress.
5. The claw enables together with the arm if the joystick is at its calibrated
   center (and no grasp hold is active); otherwise the arm still starts and the
   status says why the claw was skipped. Center the joystick and click
   **Enable claw** to enable it separately. Y=0 requests maximum/open; Y=4095 minimum/closed. Neutral Y=1810
   requests halfway. Enabling initially latches the current claw position;
   subsequent commands move gradually toward the requested opening.

**Esc**, **STOP / HOLD ALL**, the physical window's **STOP / HOLD**, or holding
the joystick switch for 0.7 seconds pauses arm and claw, and the UI stop controls also stop the
wearable vibration tests. Closing or pausing the URDF preview pauses physical
control. Closing the physical controls pauses it; reopen them with **Robot
controls** in Wearable Lab. These actions hold already-powered joints at measured
positions rather than dropping the arm by switching torque off. Released joints
are not re-enabled by a stop.

## Smart replay

**Start smart replay** (console, **Replay** panel under Assist, with the switch on **Smart replay (Gemini)**) is a separate action from plain replay. Plain replay
is unchanged: it repeats the recorded joint motion exactly. In a smart replay, Gemini (the `assist` process)
chooses what the arm does, one action at a time, and corrects itself when something goes wrong.

1. Record a motion as usual. Grasps and releases sensed by the gripper are marked on the recording, and it is
   reduced to up to 32 waypoints (start, end, every grasp/release, an even fill).
2. Gemini studies the camera pictures taken during the recording. The console shows what it understood.
   Read it, then click **This is the task - confirm**.
3. **Start smart replay.** The arm holds still until Gemini answers. Each turn Gemini sees the camera, the
   arm's state and the result of its last action, and chooses one of:

   | Action | What the arm does |
   |---|---|
   | `look` | Hold still for a fresh picture; optionally pan up to 25 degrees to look around |
   | `go_to_waypoint` | Move to a recorded pose. The gripper is not changed |
   | `play_segment` | Replay the recording between two waypoints at recorded speed, gripper included |
   | `nudge` | Move the gripper tip up to 5 cm left, right, forward, back, up or down |
   | `move_joints` | Turn joints by up to 10 degrees each |
   | `grip` | Close (reports whether an object is held) or open |
   | `ask_operator` | Hold and show a question; **Continue** resumes |
   | `done` | Hold the final pose and end the run |

A missed grasp, a dropped object, a stalled move or a refused action does not stop the run: the arm holds
where it is and the problem is reported to Gemini as the result, so it can retry, look around or back off.
Four failures in a row hand over to the operator.

What Gemini cannot change: every target still passes the same speed, lead and calibrated-range limiter as
live control. A joystick press, **Stop**, stale wearable input, torque loss, the load stops and a tracking
error end a smart replay at once, as they end a plain replay. A run also ends after 60 actions, 10 minutes,
or 2 minutes without an answer from Gemini.

`nudge` is solved from the URDF's forward kinematics (`reach.py`); it has not been tuned on the arm yet, so
check the first nudges in each direction with a hand near **Stop**.

Smart replay needs the full demo (`demo.ps1 -Controller imu`): it reads Gemini's actions from the assist
process on port 5563 and refuses to start without that link.

## Record and replay

The PAUL console has a **Replay** panel under Assist (left column). The dashboard
button and physical joystick button use the same gestures:

- Double-click within 350 ms to start recording; double-click again to finish.
- Single-click to return to the recorded first pose, then play the recording once.
- While replaying, click **Stop replay** or press the joystick button to stop.
  **Stop / hold**, Esc and holding the joystick button for 0.7 seconds also stop motion.

Enable live control before recording, and finish any initial arm alignment.
Recording captures measured arm positions, claw positions/closing intent during a
grasp and, if enabled when recording
starts, the slider position. Each new recording replaces the previous one. The
latest take stays in memory through IMU/robot reconnects, but not an application
restart. Recordings last at most 120 seconds; a take shorter than 0.1 seconds
cannot play. Single clicks wait 350 ms to distinguish them from double-clicks.

Center joystick X/Y before replay. Play enables the recorded motors, first
latching their measured positions. It waits for the first pose to settle before
starting the recorded timeline. A detected closing grasp satisfies the gripper's
arrival check; an opening obstruction or an arm joint stall still fails. Playback
uses the current dashboard speed limits; at completion it holds the final pose
and leaves live following off. If holding an object, the claw continues regulating
that grasp until Stop, contact loss, a fault, or explicit live claw enable. Stop, stale
inputs, torque loss and sensor resets interrupt replay and never automatically
resume it. Optional software load stops apply when enabled. A slider recording
requires a valid current rail reference; COM reconnects clear that reference.

After an IMU reconnect, keep the sensor straps in the same places, return to
pose A and choose **Recenter arm**. This retains the saved A/B mounting and learned
elbow/shoulder ranges, including after restarting the app. **Capture pose A / Redo
A/B** starts a new calibration; use that if a strap moved or sensor-axis settings
changed. Recenter restores the orientation reference; enable live control or play
explicitly afterward.

## Live tuning

If COM6 opens but motors do not reply during startup, the controller makes up
to three read-only connection attempts, 0.5 seconds apart. It still verifies all
six IDs and model numbers. After a connection/read failure, **Reconnect robot**
is available in the PAUL **Robot speed & load** panel and the desktop physical
controls. It closes out the old worker before starting another one, discards
queued movement enables, and clears the slider's session reference. Reconnection
does not enable arm, claw, or slider motion. Full communication errors remain in
`wearable_test/runtime.log`.

The PAUL web console's **Robot speed & load** panel adjusts arm speed, claw
speed, and both raw-load thresholds. Edit values and press **Apply settings**;
the panel shows confirmed values only after the serial worker applies them.
Settings last this controller session. Changes do not enable or resume motion.
The **Software load stops** switch defaults **off**, matching the absence of
PAUL-style instantaneous load stops in the standard LeRobot SO-101 follower.
Turning it on activates the arm (650 by default) and gripper (350 by default)
thresholds; one reading at or above a threshold pauses all following. A load
fault now includes the joint, reading and threshold. No servo EEPROM, thermal,
current or torque-protection registers are changed by this panel.

**Gripper contact sensing** requires both sustained closing load and 120 ms of
nearly stopped jaw movement before reducing closing effort. Normal running load,
acceleration and gearbox friction do not reduce the travel command while the jaws
are moving. Confirmation tolerates 12 ticks of jitter/compression and requires a
remaining closing gap greater than 12 ticks, except at the closed endpoint. This
keeps normal settling at a requested aperture from blocking the next closing input.
After confirmation it shows **Holding object** and removes accumulated closing
lead, regulating a small position bias (at most 12 encoder ticks farther closed).
Resistance within 20 ticks of the closed endpoint also receives pressure relief,
without labeling an empty close an object. An isolated load spike does not latch.
Load feedback eases the squeeze above the chosen holding level and increases it
below that level within this bound. An opening request releases the latch and
discards the old closing lead immediately. Free-motion speed is unchanged.

**Grip contact load** defaults to 180 raw counts; **Grip holding load** defaults to
120. Both can be adjusted in the PAUL panel with **Apply settings**, independently
of software load stops. Holding load must be at or below contact load. These are
servo-load estimates, not calibrated force measurements; a blocked closing jaw
can also be a jam, so the status is inferred contact. Actual firmware overload,
temperature/electrical errors, torque loss, stale data and Stop still stop control.
The controller does not mask servo errors or raise its hardware protection limits.

The same panel has **Joystick X → slider** controls. Select **Release slider**,
place the carriage manually at its physical 0% or 100% endpoint, and click the
matching endpoint button. Then center joystick X and select **Enable X control**.
Positive X moves toward 100%, negative X toward 0%, and center holds the current
position. Y still sets claw position. A/B or recenter must be complete before
enabling. Stop/Esc, holding the joystick switch, stale input and controller shutdown
release ID-7 torque; re-enabling is explicit.

The slider shares the arm worker's open COM connection. It uses saved travel
and direction from `leslider_control/calibration/robots/so101_slider_follower/`
and checks Phase 28, position mode, zero homing and multi-turn angle selectors
before allowing control. It does not silently change those firmware settings.
Its session reference is not persisted: reference again after restart or power
loss. It retains rail bounds, counter-jump, wrong-direction and stall checks.
The host command rate is capped at 600 ticks/s, with a 320-tick measured lead
allowance, using the existing slider tester's bounds.

Open **Live tuning...** in the Physical SO-101 window and choose a feel along
**More control → More speed**. The five choices are repeatable: each sets speed,
smoothing and all five joint lead allowances together. **More control** and
**More speed** move one step at a time; choosing a named button jumps directly
to that step. **Fast** matches the shipped startup settings.

| Feel | Tradeoff | Speed (degrees/s) | Smoothing (ms) | Lead: other joints / shoulder lift and elbow (degrees) |
| --- | --- | --- | --- | --- |
| Precise | Slow and steady; more delay | 30 | 65 | 2 / 4 |
| Controlled | Gentle; less delay than Precise | 45 | 50 | 2.5 / 4.5 |
| Balanced | Moderate response | 120 | 20 | 12 / 18 |
| Quick | Fast, short response filter | 180 | 10 | 20 / 30 |
| Fast | Default, minimal filtering | 240 | 5 | 30 / 40 |

These names describe the requested feel, not measured positioning accuracy.
All five presets select a 100 Hz command loop. The IMUs still publish about 50 Hz. Use
**Advanced...** for individual settings, including other control rates. An
advanced combination that does not match a preset is shown as **Custom**;
opening/reopening either window never silently replaces it. Choosing a preset
replaces all individual tuning values, including pending slider edits.

No firmware upload or re-alignment is needed for a tuning change. The worker
applies the latest settings together between control cycles. Advanced sliders
apply while following (throttled to 75 ms); number boxes apply on Enter or leaving
the box. Changes cannot build a queue of old movement commands.

| Control | Allowed values | Startup default |
| --- | --- | --- |
| Arm speed | 10–360 degrees/s | 240 |
| Smoothing | 1–100 ms | 5 |
| Control rate | 20–100 Hz | 100 |
| Each arm joint's lead | 1–45 degrees | 40 for shoulder lift/elbow, 30 for the others |

**Reset to startup** restores all eight underlying controls. Settings last only
this process; reopening the tuning window keeps them, restarting reloads
`wearable_config.json`. The status distinguishes queued changes from applied ones.

The default is **Fast**. In Advanced,
if the physical panel shows **RATE**, the host speed
cap is active. If **LEAD** persists, the motor is lagging its command: try increasing
that joint's allowance by 0.5 degrees while watching its movement and load.
Less smoothing can add jitter. More lead permits a larger position error and a
stronger correction; it does not increase the motor's torque limit. Reducing lead
immediately brings the command inside the tighter allowance, which can brake it.
Compare the requested control rate with the actual Hz: 100 Hz does not create new
IMU samples between the sensors' roughly 50 Hz reports.

Tuning never enables or resumes motion. Alignment keeps its 90 degrees/s rate;
saved joint ranges, tracking checks, input watchdogs and Stop remain
active. Claw settings, servo PID and EEPROM are not changed. Closing just the
tuning window keeps following; closing the physical panel pauses it.

## Mapping and bounds

The physical worker uses `wearable_test/retarget.py`, the same calibrated limb
geometry, URDF joint transforms, inverse kinematics, and direction switches as
the preview. Default `mapping_mode: "absolute"` converts those joint angles to
servo targets through the saved `joint_mapping.json` directions, offsets and
scales plus each joint's `calibration.json` travel. This inverts the original
LeRobot/RViz range mapping in `Documents/lerobot/scripts_local/rviz_udp_lerobot_bridge.py`
and `ros2_so101_ws/src/so101_description/scripts/real_state_udp_receiver.py`.
The saved 33° wrist-roll registration locates its center. Wrist increments now
use actual encoder degrees, removing the old 360/320 range amplification.
Both preview and physical following apply **2× hand roll**. A separate
`servo_trim_deg.wrist_roll: 45` adds **45 physical degrees counterclockwise
viewed from behind the wrist, looking toward the claw**. It does not change
elbow/wrist flex or claw opening. The telemetry inverse removes this trim so
the displayed pose can be compared directly with the requested preview pose.
No EEPROM or homing offsets change.

### Learn the full shoulder and elbow range

After A/B or recenter, use these controls in either the web calibration panel
or Wearable Lab. Each pauses motion and uses the same 3-second countdown and
2-second stable capture:

1. **Learn elbow bend / C · Bent elbow:** bend to your comfortable maximum.
   The angle between calibrated bicep and forearm directions is the input.
   Straight (0°) maps to the saved robot elbow minimum; the captured maximum
   maps to its maximum. Intermediate bends interpolate linearly, independently
   of shoulder elevation. Captures below 45° are rejected.
2. **Learn shoulder reach / D · Arm raised:** raise your straight arm to your
   highest comfortable reach, at least 30° above horizontal. The lowered pose B
   maps to one shoulder endpoint, forward pose A keeps its existing reference,
   and the raised pose maps to the opposite endpoint. Each side interpolates
   separately. Old mounting-only records use -90° for the lowered endpoint.
3. **Learn pan left / E** and **Learn pan right / F:** keep your torso facing
   forward and capture a comfortable reach to each side, with your wrist away
   from your shoulder. Each side must be 15–150° from pose A. Capture both to
   activate pan scaling: pose A stays centered, left reaches one robot pan limit,
   and right reaches the other. Each side scales independently; a smaller human
   sweep reaches full robot travel. Flex/roll calculations use the unamplified
   reach direction, so pan gain does not distort the other joints.

These learned ranges prioritize access to the robot's joint travel over exact
human wrist position. Wrist flex compensates for the new shoulder/elbow pose
as far as its own range allows. Without a learned range, that joint keeps the
original reach-based IK mapping. Preview and physical control use the same
mapper. Captures are saved with mounting, survive recenter/restart, and are
cleared by a new A/B mounting calibration. Failed captures keep previous ranges.

Mounting, dimensions, elbow/shoulder ranges and both pan endpoints save
automatically to `wearable_test/calibration.local.json`. The calibration panel
shows save/load success or an error, along with the file path. On restart, the
app restores these settings (using a separate `settings.local.json` if present)
and requires only **Recenter arm** in pose A to restore the IMUs' world reference.
Save failures keep the previous file. Changed straps or sensor-axis settings
still require A/B; saved mounting is rejected if explicit settings do not match.

The previous relative approach added wearable angle changes to an arbitrary
robot start pose. That preserved a folded offset; live logs showed the elbow and
wrist repeatedly clipping at their saved bounds. Absolute mapping removes that
start-pose dependence. The existing mapping is a calibration reference, and its
physical registration must still be checked with a small real movement. The
gripper retains its separate absolute joystick mapping and saved servo range.

For troubleshooting only, `mapping_mode: "relative"` retains the old enable-pose
anchoring. Restart after changing configuration; absolute mode is recommended.

`wearable_config.json` contains the physical target rate (initially **240°/s**),
maximum goal lead (**30°** beyond measured position, **40° for shoulder lift and
elbow** via `joint_lead_deg`), raw motor-load threshold
(**650 counts**, used only when `load_stops_enabled` is true), and per-joint raw servo directions. The existing direction
convention is +1 for each arm joint. After stopping control, a confirmed reversed
physical joint can be changed to -1 in `raw_direction`, then restart the app.
The target rate is a host command limit, not a hardware velocity controller.

The controller targets **100 Hz** (`control_hz`) using Python's high-resolution
sleep on Windows, with a **5 ms** response filter (`response_ms`).
Timing and slew calculations use `perf_counter`, avoiding Python 3.12's coarse
15.625 ms Windows monotonic clock; input freshness keeps the sensor clock.
The previous 60°/s, 35 ms, 3°/5° settings heavily clipped requests: recent logs
showed shoulder request errors near 30° and wrist-roll errors over 40° while
commands were held close to the encoders. The wider command allowance now lets
a 20° request reach the complete desired target. A regression checks both
directions within 120 ms of host updates, including stationary encoder input;
that is a command-path measurement, not measured hardware speed. Other tests
compare preview targets, inverse-mapped measured angles and learned endpoints.
No servo PID, torque strength, EEPROM, or firmware settings are changed.

The physical panel displays loop Hz, USB read time, **Preview pose** and **Actual
pose** in matching URDF degrees, **Sent raw** (motor command), and the remaining request gap
in physical motor degrees. `RATE` means the speed cap is active, `LEAD` means
the command is being held near a motor that is lagging, and `RANGE` means the
request exceeds saved travel. Persistent LEAD calls for inspecting motor load,
power and mechanics; increasing mapping gain or removing the lead cap won't
resolve a motor that cannot follow. Don't widen calibration limits to force
motion. Timing and active desired/sent/measured values are logged every 10 seconds.
Motion quality still needs an operator-controlled movement check; read-only
timing cannot establish smooth motion under load.

Goals stay inside `calibration.json` ranges. Enable verifies saved homing offsets,
position mode, and current positions against the hardware without writing
calibration or EEPROM. The IMU controller's claw commands now run at **200% of
travel per second** with **20% lead**: a 0.5-second full-travel command in the
absence of motor lag, ten times the previous command rate. Configure these with
`claw_speed_per_s` and `claw_lead_fraction`. The legacy joystick dashboard keeps
its own defaults. With software load stops enabled, raw gripper load at/above
`gripper_max_load` (default 350) pauses control. Software load stops default **off**
in the IMU controller; this does not change any servo firmware settings. These load counts
are servo effort, not measured force or a calibrated collision detector.

The physical worker reads fresh IMU/joystick snapshots directly from the existing
serial owners and computes geometry using an immutable copy of the calibrated
model. Brief Tk/window delays no longer freeze the physical input. Calibration,
direction changes and Stop still come from the UI. Board epoch changes are
checked independently, so a reset cannot use an old reference during a UI delay.

The controller stops following on stale sensor input (300 ms), an unresponsive
UI (1 second), slow motor
telemetry (250 ms), high load, unexpected torque loss, invalid positions,
tracking errors, a reset/recenter, a direction change, or a serial failure.
It checks input freshness again after hardware reads and immediately before
motion writes. Pending enables are cancelled by Stop. The worker keeps no queue
of old movement targets and does not depend on renderer FPS.

**Physical motion never automatically resumes after a fault or reset.** Recenter
if needed, hold still, and enable again; this captures a new alignment pose. Calibration
actions and editing arm settings pause physical control. A USB failure can
prevent sending the final hold; servos can continue toward their last bounded
goal. Joint limits do not provide collision avoidance for the table, objects,
people, or the arm itself. No physical motion has been verified by the automated
tests; the first small real movement is the operator's direction check.

## Verification

`robot-check` reads all six motors and checks saved homing offsets, position modes
and ranges. It never writes a position or torque setting.

```powershell
# Fake servo bus only: no physical robot or ESP32 ports are opened.
uv run robot_control/test_wearable.py

# Read-only timing/settings diagnostic. Close Wearable Lab first to free COM6.
uv run robot_control/diagnose.py --port COM6
```

Regression coverage includes read-only startup/shutdown, no-jump enabling,
URDF-to-servo direction/rate/lead bounds, independent claw enable, calibration
mismatch, atomic live tuning/reset, tuning during hardware reads, reduced-lead
transitions, stale input during hardware reads, UI stalls, slow robot telemetry,
cancelled enables, torque/load faults, and explicit re-enable after recovery.
Regression tests also replay the actual screenshot's residual errors, verify
alignment progress/settling/deadlines, and exercise fresh serial input during a
brief UI stall while retaining stop-on-reset/stale-input/button behavior.
Local reset and physical-controller events are in `wearable_test/runtime.log`.
