# Wearable Lab → physical SO-101

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
   in a comfortable pose. For a similar visual pose, arrange the unpowered,
   supported robot approximately like the preview before enabling torque.
4. In the physical controls, click **Enable arm · anchor here**. It latches the
   current measured robot positions and current mapped wearable pose. It follows
   subsequent angle changes, so it does not move to URDF zero or to a predefined
   starting pose on enable. Move one joint slightly and confirm direction first.
5. Release the joystick to its calibrated center and click **Enable claw** when
   ready. Y=0 requests maximum/open; Y=4095 minimum/closed. Neutral Y=1810
   requests halfway. Enabling initially latches the current claw position;
   subsequent commands move gradually toward the requested opening.

**Esc**, **STOP / HOLD ALL**, the physical window's **STOP / HOLD**, or pressing
the joystick switch pauses arm and claw, and the UI stop controls also stop the
wearable vibration tests. Closing or pausing the URDF preview pauses physical
control. Closing the physical controls pauses it; reopen them with **Robot
controls** in Wearable Lab. These actions hold already-powered joints at measured
positions rather than dropping the arm by switching torque off. Released joints
are not re-enabled by a stop.

## Mapping and bounds

The physical worker uses `wearable_test/retarget.py`, the same calibrated limb
geometry, URDF joint transforms, inverse kinematics, and direction switches as
the preview. It follows **joint-angle differences from the enable pose**, scaled
to servo ticks. This anchoring preserves the robot's starting pose; it is not
absolute registration between the human/URDF and physical arm. The gripper uses
the existing absolute joystick position mapping and actual saved servo range.

`wearable_config.json` contains the physical target rate (initially **10°/s**),
maximum goal lead (**3°** beyond measured position), raw motor-load threshold
(**650 counts**), and per-joint raw servo directions. The existing direction
convention is +1 for each arm joint. After stopping control, a confirmed reversed
physical joint can be changed to -1 in `raw_direction`, then restart the app.
The target rate is a host command limit, not a hardware velocity controller.

Goals stay inside `calibration.json` ranges. Enable verifies saved homing offsets,
position mode, and current positions against the hardware without writing
calibration or EEPROM. Claw commands are limited to 20% of calibrated travel per
second and 2% lead; raw gripper load at/above 350 pauses control. These load counts
are servo effort, not measured force or a calibrated collision detector.

The controller stops following on stale input/UI heartbeat (300 ms), slow motor
telemetry (250 ms), high load, unexpected torque loss, invalid positions,
tracking errors, a reset/recenter, a direction change, or a serial failure.
It checks input freshness again after hardware reads and immediately before
motion writes. Pending enables are cancelled by Stop. The worker keeps no queue
of old movement targets and does not depend on renderer FPS.

**Physical motion never automatically resumes after a fault or reset.** Recenter
if needed, hold still, and enable again; this captures new anchors. Calibration
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
```

Regression coverage includes read-only startup/shutdown, no-jump enabling,
URDF-to-servo direction/rate/lead bounds, independent claw enable, calibration
mismatch, stale input during hardware reads, UI stalls, slow robot telemetry,
cancelled enables, torque/load faults, and explicit re-enable after recovery.
Local reset and physical-controller events are in `wearable_test/runtime.log`.
