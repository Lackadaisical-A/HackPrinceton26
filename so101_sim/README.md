# Three-IMU SO-101 simulation

This is a **kinematic URDF pose preview**. It reads the dashboard's Firebase
data and animates the model using the URDF joints and limits. It never opens
robot hardware or sends motor commands. It does not validate dynamics,
self-collision avoidance, physical servo calibration, or contact safety.

## Placement for this prototype

| Stream | Placement | Purpose |
|---|---|---|
| s3_imu1 / 0x4A | Rigid strap on the upper arm, above the elbow | Shoulder swing and elevation |
| s3_imu2 / 0x4B | Rigid strap on the forearm, below the elbow | Elbow flex relative to upper arm |
| wroom / COM7 | Back of hand, on a glove or hand strap beyond the wrist joint | Wrist bend and twist |

Avoid placing sensors directly on the elbow/wrist crease or loose clothing.
Strain-relieve the wires. The hand sensor must move with the hand, not merely
sit on a forearm cuff. Keep a vibration coin mechanically separate from an
IMU when possible to reduce vibration coupling.

This placement covers shoulder/elbow/wrist with three sensors. The torso is
unmeasured, so keep it still and face the same direction. A fourth sensor on
the chest would allow torso-relative shoulder control. Three IMUs do not
measure finger separation; use the G key for the gripper for now.

## Mounting axes and neutral pose

For the default mapping, reach the arm comfortably forward with elbow nearly
straight, wrist straight, and palm down. Align the sensor **+X arrows along
the arm toward the hand/fingers**, and their **+Z axes upward in this pose**.
All three boards should have the same axis orientation in this reference pose.
If a breakout uses different markings, consult its axes diagram or adjust
the mapping axes; do not assume the connector direction is +X.

Each IMU has its own orientation reference. Neutral capture removes each
sensor's starting orientation, but cannot resolve arbitrary mounting-axis
misalignment. This is a useful control mapping, not calibrated human motion
capture. Game rotation vectors can drift in heading; recapture neutral as
needed. Sliding the hand without rotating the body segments is not measured
as an independent position input.

## Run

Keep the existing dashboard running. If needed, in terminal 1:

```powershell
.\sensor.ps1 dashboard
```

In terminal 2:

```powershell
.\sensor.ps1 sim
```

The first launch installs/builds PyBullet for Python 3.12 and can take several
minutes on Windows. The simulator uses Firebase and does not compete for COM7/8.

1. Focus the PyBullet simulation window.
2. Hold the neutral pose described above, then press **C** to capture and enable.
3. Slowly move one joint at a time and check the virtual arm's response.
4. **Space** pauses/resumes. **C** recaptures neutral. **Q** quits.
5. Press **G** to toggle the simulated gripper open/closed.

It starts paused. Missing/stale sensor data pauses and holds the current pose;
when data returns, use Space or C to resume. Joint movement is limited to
60 degrees/second. Readings come through the cloud, so expect latency; this
path is for preview, not a hard real-time physical-arm control loop.

## Adjust the mapping without flashing

Edit `mapping.json`, then restart the simulator. `gain: -1` reverses a joint,
smaller gain reduces travel, and `offset_deg` sets its neutral robot angle.
`axis` selects the sensor neutral-frame X/Y/Z twist used for that joint.

| Robot joint | Input rotation |
|---|---|
| shoulder_pan | Upper arm about neutral Z |
| shoulder_lift | Upper arm about neutral Y |
| elbow_flex | Forearm relative to upper arm about Y |
| wrist_flex | Hand relative to forearm about Y |
| wrist_roll | Hand relative to upper arm about X (includes forearm pronation) |

Human shoulders have more freedom than this robot. These projected rotations
are intentionally approximate; validate signs and ranges in simulation before
designing any real-arm controller. Gripper URDF angles are radians, not
LeRobot's normalized 0..100 gripper commands.

## Model and validation

Copied the local `so101_new_calib.urdf` plus its 13 referenced STL meshes from
`C:/Users/herma/Documents/lerobot/SO-ARM101-6DoF-main/Simulation/SO101`.
Model provenance: [TheRobotStudio SO-ARM100/SO101](https://github.com/TheRobotStudio/SO-ARM100/tree/main/Simulation/SO101),
generated from Onshape via onshape-to-robot. The original local files were not
modified. This model has five arm joints plus the gripper, despite the source
folder's "6DoF" name. New-calibration zeros are near joint-range midpoints.

```powershell
uv run --python 3.12 python -m unittest discover -s so101_sim -p test_mapper.py
uv run --python 3.12 so101_sim/sim.py --check
```

The latter loads all meshes and renders `preview.png` without Firebase or
physical hardware. [PyBullet documentation](https://github.com/bulletphysics/bullet3/blob/master/docs/pybullet_quickstart_guide/PyBulletQuickstartGuide.md.html).
