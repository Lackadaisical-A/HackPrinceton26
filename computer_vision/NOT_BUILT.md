# PAUL vision: not built, not verified

What this module still lacks: features that are planned but do not exist, and things that exist but have not been checked on the real setup. What is built is described in `README.md` (running and tuning) and `CLAUDE.md` (how the code fits together). The original brief is `KICKOFF_PROMPT.md`.

This is the state of the code on 14 November 2026 at 09:22. The command and M3 entries were brought up to date at 09:55 the same morning, after vision, `../bridge` and the web console were run together. When an item is built or verified, delete it here and describe it in `README.md` and `CLAUDE.md`.

## At a glance

| Milestone | Item | State |
|---|---|---|
| M2 | ArUco stations | not built |
| M2 | Commands | `select_target` and `clear_target` are built; `set_phase` is received and ignored |
| M2 | Pressure input, mock publisher, serial bridge | not built |
| M2 | Phases after `approach` | not built |
| M3 | ZMQ to WebSocket bridge | built in `../bridge` and run with vision and the web console; what that run did not cover is below |
| M4 | User labelling of objects | not built |
| M5 | Speech to text | not built |
| | CUDA profile on the demo machine | never run |
| | Camera orientation on the arm, class list, thresholds | not checked on our setup |

The milestones depend on each other in this order: M4 needs commands (M2) and, for the web console, the bridge (M3).

## M2: stations, commands, pressure, phases

### ArUco stations: not built

- Detect the markers that mark the pick and drop-off stations.
- Plugs in as a second detector that returns `Detection(..., source="aruco")`. The tracker and selector take any `Detection`, and the message validator does not restrict `source`.
- `cv2.aruco` is part of `opencv-python` 5, so no new dependency. Check the `ArucoDetector` API through Context7; it changed in OpenCV 4.7.

### Commands: partly built

Built (`paul_vision/commands.py`): `CommandReceiver` connects to `transport.command_sub` (port 5557) and is polled on every pass of the main loop. `select_target` restricts the target to one name, which must be in `detector.classes`; anything else is logged and ignored. `clear_target` lifts the restriction.

Not built:

- **`set_phase`.** It is received, logged as unsupported and ignored. It needs the phase machine below.
- **A second sender.** `config.yaml` has the console bridge bind port 5557, with voice to follow, but only one process can bind a port. Not decided: voice sends through the bridge, or vision connects to a second endpoint.
- `tag_text` and `tag_visual`: see M4.

### Pressure: not built

- A subscriber on `transport.pressure_sub` (port 5556). Payload, from the brief: `{"t": ..., "raw": [r0, r1, r2, r3], "norm": [0..1 x4]}`.
- `mock_pressure.py`: a stand-in publisher. A key toggles contact, or it replays a log.
- `pressure_bridge.py`: reads CSV over USB serial from the sensor microcontroller and publishes `pressure`. A stub until the hardware is ready.

### Phases: only `search` and `approach` exist

- Wanted: `search → approach → grasp → carry → place → release`.
- Contact is sustained pressure above a threshold. The threshold and the duration go in `config.yaml`.
- If contact is lost during `carry` away from the drop station, emit a `dropped` alert. The `vision` message has no field for an alert yet.
- During `carry` the target switches to the drop-station marker.
- Plugs in at `PhaseMachine.update` in `phase.py`. `phase.PHASES` already lists all six phases, so the message validator accepts them.
- **Constraint found in testing:** the detector loses an object that fills the frame, so vision says "Target lost." just before a grasp. The `grasp` phase has to rely on pressure, not on the target staying visible.

### `robot` topic: not built, optional

Servo position and load from the arm controller, on `transport.robot_sub` (port 5558). Vision must never open the servo bus itself.

## M3: ZMQ to WebSocket bridge: built, not checked on the wrist camera

The bridge is in `../bridge` (its `README.md` says how to run it and what it accepts), and the vision side is `paul_vision/stream.py` and `paul_vision/commands.py`. All three were run together with the web console on 14 November; `README.md` here describes that run under "Web console". Not covered by it:

- **The wrist camera.** It was not plugged in. The picture came from the laptop's built-in camera (`--source 0`).
- **Frame rate with a viewer.** In the one run where the built-in camera delivered 30 FPS, vision held 29 to 30 FPS with the console watching. `detect` rose from about 15 ms to about 26 ms while headless Chrome was running on the same integrated GPU. Not measured on the demo machine or with an ordinary browser window.
- **How far the overlay and the picture are out of step.** They reach the console separately (WebSocket and MJPEG). Not measured on a moving object.
- **Box edges that coincide.** `messages.validate_vision` accepts a box with `x1 == x2` or `y1 == y2`; the console's validator requires `x1 < x2` and `y1 < y2` and would drop that message. Not seen in a run, and not decided which side should change.

## M4: user labelling of objects: not built

What exists:

- Choosing among the configured names: `select_target`, or `--target-label` at startup.
- `bake.load_promptable`: a YOLOE model whose classes can still be set (torch profile only).
- The camera stream, so the console can show the real picture.

What is missing:

- The `tag_text` and `tag_visual` commands. The brief names them (`tag_visual` with `bbox` and `name`) and never defines their payloads. The console sends neither and has no way to mark an object on its camera view.
- Applying a new class to the running detector.
- A map from the model's class numbers to the user's names.
- Saving the labels to a prompt-embedding library file and loading it at startup.

Two ways to label, per the Ultralytics YOLOE docs:

- **By name:** add a text prompt to the class list (`set_classes`). The sample clips suggest this is weak for similar objects: "pill bottle" is in the class list and almost never wins over "bottle" or "cup".
- **By example:** the user marks the object in a frame and names it. `predict(frame, refer_image=..., visual_prompts={"bboxes": ..., "cls": ...})` sets the classes for all later calls, and an export keeps them. Results come back as `object0`, `object1`, so the names are ours to keep.

Both are live only on the torch profile (the NVIDIA machine). The OpenVINO export on the laptop has fixed classes, so each new label needs a re-export of about a minute there.

Not tested:

- Whether visual prompts work with how the model is loaded here (detection only, mask branch removed).
- How to keep the four text classes alongside a visual one in the same model.
- Whether an example tells similar objects apart, for instance the jar from the water bottles in `recordings/run1.mp4`.

Suggested, not decided: make the interaction "label what is locked". The user says or clicks "this is my pill bottle" while vision has a target, and vision uses the current frame and that target's box as the example. That needs no box drawing in the console and works by voice later. Try visual prompts on `recordings/run1.mp4` before building anything, because the rest depends on whether they separate similar objects.

## M5: speech to text: not built

A separate process that sends speech to a cloud service and publishes `select_target` commands. No service is chosen. Vision never touches audio; it only receives the commands.

## Not verified

- **CUDA profile.** Never run: the development laptop has no NVIDIA GPU. FPS and latency on the demo machine are not measured. The steps for the first run are in `README.md` under "Demo machine bring-up".
- **Camera orientation on the real arm** (`camera.rotate_deg`, `flip_h`, `flip_v`). The check is in `README.md` under "Camera".
- **A camera mounted at 90° or 270°.** It needs `detector.imgsz: [640, 384]` and a new bake. That shape has never been run.
- **The class list** `[cup, bottle, pill bottle, box]`. It is a guess until the demo objects are known. On the sample clips "pill bottle" almost never wins and "box" does not match small blocks or trays.
- **Thresholds on our own footage.** They were set on public clips from other cameras. The one recording of ours, `recordings/run1.mp4`, never brought an object closer than "far", so `distance.thresholds` and the behaviour at grasp range are untested on our camera. `selection.coast_s` and `selection.conf_keep` are open; the numbers are in `README.md` under "What the sample clips showed".
- **Real consumers.** The `vision` stream has been read by `subscribe_print`, the tests and the web console (through the bridge). No run with the haptics or speech processes is recorded in this folder.
