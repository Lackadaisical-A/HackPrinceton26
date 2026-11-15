# Potentiometer + robot camera + frontend test

Run from `HackPrinceton26`:

```powershell
.\demo.ps1 -Check   # Dependencies/ports only; no hardware opened
.\demo.ps1 -DryRun  # Real pots + vision + target previews; no robot connection
.\demo.ps1          # Live arm control + vision + measured robot feedback
```

Defaults match your working command:
`esp32_wh148_slider_7dof_bridge.py --leader-port COM9 --robot-port COM6`.
That controller expects **seven pot channels including the slider**. For the
**six-pot arm/gripper sketch without the slider**, add `-Controller pots`.
The copied pot calibration matches the original. Seven-pot control retains
absolute mapping; six-pot control retains relative mapping. The original
`Documents/lerobot` checkout is unchanged.

The launcher starts or reuses vision, the WebSocket bridge and Vite, then opens
the console already connected to live data. The controller stays in the
foreground for keyboard controls and slider reference prompts. Only that
controller owns the robot serial port.

## Test procedure

1. Connect the pot reader on COM9 and the powered robot controller on COM6.
   Use `-LeaderPort` / `-RobotPort` to change these. Close the previous pot
   controller, Serial Monitor, or any other process holding those ports.
2. Run `-DryRun` first. Turn each pot and verify its target in the console.
   The Arm panel says **Arm target preview**; loads are unavailable. No robot
   port is opened. Missing/stale pot readings stop preview updates.
3. Quit with **Q**. Support the arm before starting the live command: the
   existing controller configures the servos and disables torque at startup.
   Follow the slider reference prompts if using seven-axis control.
4. Motion starts **paused**. Focus the controller terminal. **E** enables or
   pauses control, **T** disables torque, **C** recenters relative mapping,
   and **Q/Esc** quits. Pausing disables torque; it is not a powered hold.
   Keep the arm supported and its power switch accessible: a lost USB link
   can prevent a software stop.
5. Compare requested targets with measured positions and loads in the console.
   Select Cup/Bottle/etc. to filter vision. Record saves synchronized robot
   and vision telemetry. Vision provides guidance but does not steer the arm.

Pressure/haptic publishers are not added by this launcher. Their panels have
no live readings unless separate publishers are connected. Servo load is motor
effort, not gripper pressure or calibrated force. Quit stops only services the
launcher started; existing services remain running.

## Robot camera and services

The default source is **wrist**, configured as **camera 0**, the USB
**Innomaker-U20CAM-1080p-S1**. Windows DirectShow device enumeration confirmed
index 0 is the USB camera and index 1 is the integrated camera on this machine.
Use **Camera source** above the Console's video to select a detected device by
name. **Refresh** discovers newly connected cameras. Selection changes both the
video and computer vision input without restarting services. The old feed stays
active until the new camera delivers a frame; a failed switch keeps the old
source. Camera selection is disabled while vision's `--record` is active.

`-CameraSource 0` or `-CameraSource 1` sets the startup source; a reused vision
process retains its current source/config. Runtime choices last until vision
restarts. `camera.wrist_index` remains the default for the next wrist session.
Logs print when an existing service is reused.

```powershell
.\demo.ps1 -ServicesOnly  # Vision and console; no serial ports
.\demo.ps1 -VisionConfig computer_vision/runs/gpu-web-test/config.local.yaml
.\demo.ps1 -ControllerOptions @('--control-mode', 'relative')
uv run --python 3.12 leslider_control/run.py demo --help
```

| Connection | Default |
|---|---|
| Console | `http://127.0.0.1:5173` |
| WebSocket bridge | `ws://127.0.0.1:8765` |
| Robot camera | `http://127.0.0.1:18766/stream` |
| Vision telemetry / commands | ZMQ ports 5555 / 5557 |
| Robot telemetry | ZMQ port 5558 |

18766 avoids another application using 8766 on this computer. The launcher
writes a runtime vision config, logs, and launch URL under `runs/pot-test/`.
It preserves the source config and resolves its model paths. Reused services
must use the same ZMQ endpoints as that config.

Install frontend dependencies with `npm --prefix frontend ci` if needed.
`uv` manages controller/bridge dependencies. Vision uses its existing
`computer_vision/.venv` directly, preserving CUDA. For first-time vision setup,
follow `computer_vision/README.md`; this computer uses the `cu128` extra.

## Replacing pots with IMUs later

`leslider_control/telemetry.py` separates input from feedback. An IMU controller
can use `RobotTelemetry(input_source="imu")` and pass its targets/input freshness
to `update`. Vision, the relay and six-joint array order stay the same.

Telemetry runs on the controller thread at up to 10 Hz with bounded nonblocking
ZMQ sends. `robot.pos` contains measured degrees (gripper 0–100), and `robot.load`
contains signed `Present_Load / 1000`, clamped to −1..1, or `null` when unavailable.
`control` adds the input source, enabled/freshness flags, requested targets, and
`feedback: "measured" | "preview"`. Optional `slider` feedback is separate.
Dry runs label mapped targets as previews with null loads. Failed position reads
stop publication; stale values are hidden. Browser/vision commands cannot enable
or move the arm in this test integration.

## Verification

70 controller/integration tests and 251 frontend tests passed; production build
and lint passed. Tests exercise both controller loops with mocked hardware,
stale-input shutdown, recentering, feedback failures, real ZMQ/WebSocket routing,
and service cleanup. Run `.\control.ps1 test` for controller regression tests.

A separate launcher-started stack ran CUDA vision on the paper-cup replay.
The frontend and MJPEG endpoints responded, and one WebSocket received 28
labeled robot previews plus 90 vision frames in four seconds. Target/clear
commands reached vision. Closing it removed only the test services.

COM9 was absent during verification, so physical pots and arm motion have not
been tested in this integrated session. Browser visual verification was blocked
because Computer Use could not reliably determine Chrome's current URL.

The USB Innomaker robot camera was subsequently identified by DirectShow device
name and left running as the wrist source at index 0. Its MJPEG stream showed
the workbench and screwdriver; vision ran at approximately 26–29 FPS. The
integrated camera at index 1 produced black frames and is no longer selected.

The live camera selector was verified in headless Chrome: it switched to the
integrated camera and back to the Innomaker camera through the Console. The
Innomaker was left selected. Browser checks also cover pending switches, failure
retention, device refresh, recording lockout, API recovery and mobile layout.
