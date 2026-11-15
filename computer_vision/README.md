# PAUL vision

Finds objects in the wrist-camera feed, picks a target, and publishes guidance ("move so the target is centred") over ZMQ. Other processes turn that into haptic pulses and speech. Vision assists the operator and never controls the arm.

Built: frame sources, YOLOE detection, tracking, target selection, guidance, spoken summaries, the `vision` publisher with a stall watchdog, the debug overlay, the camera picture and commands for the web console; from milestone 2 the station markers (ArUco), the gripper pressure input with a mock and a serial bridge, all six task phases, the `dropped` alert and the `set_phase` command; from milestone 4 names the operator teaches while vision runs (`tag_visual`, `tag_text`, `untag`, see "Teaching vision a name"); from milestone 5 a voice process that turns utterances, as text, into commands (see "Voice commands"); and contact decided from the gripper servo's load in the arm controller's `robot` messages, shipped off until real grasps are recorded (see "Gripper load"). The milestone 2 parts have only run on a bench: no real pads, printed marker or wrist camera yet. Milestones 4 and 5 have run on recorded clips and, through the team launcher `../demo.ps1`, live on the laptop's built-in camera with typed voice lines (see "The team launcher"); never on the CUDA machine or the wrist camera, and no command has been spoken to a live microphone. The CUDA profile has run with the web console on the NVIDIA machine, but only before milestones 2, 4 and 5 (see "NVIDIA web UI verification" below). That, and what is not built (the team's speech recognizer, demo-object accuracy), is listed in `NOT_BUILT.md`.

## Run it

```powershell
uv sync                                         # once
uv run python -m paul_vision.bake               # once, and after changing detector.classes, imgsz or weights
uv run python -m paul_vision --source wrist     # overlay window + publisher
uv run python -m paul_vision.subscribe_print    # second terminal: prints what subscribers receive
uv run python -m paul_vision.voice              # voice commands as typed or piped text (see "Voice commands")
uv run pytest                                   # no camera, GPU or pads needed
uv run python scripts/eval_clips.py sample_videos   # report on recorded clips (see "Testing on recorded clips")
```

`--source` takes `wrist` (the camera at `camera.wrist_index`), a camera index such as `0`, or a video file.

| Flag | Effect |
|---|---|
| `--no-overlay` | Headless. |
| `--no-stream` | Do not serve the camera picture to the web console. |
| `--max-frames N` | Stop after N frames and print the per-stage timing table. |
| `--record FILE` | Also save the frames to `FILE` (`.avi` or `.mp4`); replay it later with `--source FILE`. Switching cameras from the console is off while recording. |
| `--video-end exit\|loop\|hold` | What a video file does at its end. `hold` stalls the feed so subscribers see `degraded`. |
| `--target-label LABEL` | Only target this label: one of `detector.classes` or a name taught earlier (see "Teaching vision a name"). |
| `--backend`, `--device` | Override the detector profile, e.g. `--backend torch --device cpu`. |
| `--config FILE` | Use another config file. |

Overlay keys: `q` or `Esc` quits, `s` saves a snapshot to `snapshots/`, `p` or space pauses (publishing stops, so subscribers see `degraded`).

The overlay's top line shows the status and the phase, with `(set)` after a phase that came from `set_phase`. The line under it shows the gripper pads: `grip: no pressure data`, `grip: deciding 0.40`, `grip: holding 0.61` or `grip: open 0.03` (see "Gripper pressure"); with `gripper_load.enabled` it reads `grip (load): ...`, and `grip (load): no load data` while no measured load arrives (see "Gripper load"). Station markers that are not the target are drawn in magenta. A red `ALERT: DROPPED` banner shows while the alert is held.

The startup banner lists what this run uses: the object detector profile, the station markers (or that they are off), the grip source (`pressure:` and the pads' endpoint, or `grip:` and the gripper load's), the command endpoints, and on the `labels:` line the names taught so far.

The first start takes 10 to 20 s on the Intel laptop while OpenVINO compiles the network for the GPU. With taught names but no OpenVINO export for them yet, the start builds that export first (see "Teaching vision a name").

## Two detector profiles

The profile is chosen at startup and printed in a banner; check that it says what you expect.

| Machine | Profile | How |
|---|---|---|
| NVIDIA GPU (demo machine) | `torch on CUDA` | PyTorch, half precision |
| Intel laptop | `openvino on Intel GPU` | OpenVINO export on the integrated GPU, CPU if there is none |

Both use `yoloe-26s`, so thresholds tuned on one machine carry over to the other. The station markers are found by OpenCV on the CPU in both profiles.

`python -m paul_vision.bake` turns the class list in `config.yaml` into prompt embeddings (and an OpenVINO export for the Intel profile) under `models/`. The first bake downloads the weights (30 MB) and a text encoder (243 MB), so do it on good wifi. After that, startup needs no network. `models/` is gitignored: bake on each machine, or copy the folder across.

Ultralytics itself sends anonymous usage events to Google Analytics by default, from every process that loads it (vision, `bake`, and the label worker of "Teaching vision a name"), whenever its `sync` setting is on and the machine is online. The events carry the library version, the CPU model, the device, the task and the model file name; no images. `yolo settings sync=False` is the documented way to turn it off. That setting lives in Ultralytics' own settings file, outside this folder, and has not been changed; whether to change it is the team's decision.

### Demo machine bring-up

Choose the extra for the installed driver. The RTX 2080 Super Max-Q machine was verified with driver 573.91 and `--extra cu128` (PyTorch 2.11.0+cu128). Use `cu128` instead of `cu130` in every command below on that machine. CUDA 13 requires driver 580 or newer; see [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) and [PyTorch builds](https://pytorch.org/get-started/previous-versions/).

First run:

1. `uv sync --extra cu130`
2. `uv run --extra cu130 python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` must print `True`. If it prints `False`, update the NVIDIA driver (the wheels are built for CUDA 13.0).
3. `uv run --extra cu130 python -m paul_vision.bake` (or copy `models/` from the laptop first to skip the downloads).
4. `uv run --extra cu130 python scripts/list_cameras.py`, then set `camera.wrist_index` in `config.yaml`.
5. `uv run --extra cu130 python -m paul_vision --source wrist --no-overlay --max-frames 300`. The banner must say `torch on CUDA`; note the FPS and latency, and `detect aruco` in the log line (station markers are on by default).
6. `uv run --extra cu130 python -m paul_vision --source wrist` and check the orientation (below).

Always pass the chosen extra (`--extra cu128` or `--extra cu130`). A plain `uv run` re-syncs the environment to the CPU build of PyTorch.

### NVIDIA web UI verification, 14 November 2026

Tested commit `d156242` with the added `cu128` extra, Python 3.12, PyTorch 2.11.0+cu128, YOLOE-26s and Chrome. The detector banner reported `torch on CUDA 0`, and an independent CUDA tensor calculation succeeded.

- Camera 0: 1280x720 MJPG, 25.4 messages/s over a 596-message sample with the console watching. Capture-to-subscriber age was 55 ms median and 74 ms p95. These are telemetry timings, not measured screen-display latency.
- Camera 1: 300 frames in 30.1 s (10.0 FPS), capture-to-publish mean 38 ms and p95 47 ms. The console correctly showed the low-rate warning.
- The documented paper-cup replay showed the cup box, confidence, arrow, distance and captions. A 927-message sample had 681 cup locks and zero contract-validation errors. Replay ran about 23–30 FPS across the test; this is a smoke test, not an accuracy score.
- The console's Bottle and Cup buttons reached vision and changed its target filter; Any object sent `clear_target`. Verified in both bridge and vision logs.
- Stopping vision hid guidance and showed the missing-data warning. Restarting vision restored telemetry and the picture without reconnecting the browser. The replay's first inference briefly triggered the watchdog before recovering.
- The browser accepted more than 3,500 vision messages with zero rejections. Test suites passed: vision 271, bridge 47, frontend 246; frontend production build passed.

The local session uses stream port **18766** because another application occupies 8766. Its config and logs are in the ignored `runs/gpu-web-test/` directory. To restart vision with this machine's saved config:

```powershell
uv run --extra cu128 python -m paul_vision --config runs/gpu-web-test/config.local.yaml --source 0 --backend torch --device 0 --no-overlay
```

Start the bridge and frontend as described below, then select Live bridge at `http://127.0.0.1:5173/#/signals`, with `ws://127.0.0.1:8765` and camera URL `http://127.0.0.1:18766/stream`. Camera 0 was left running. Robot movement, pressure/haptic integration, camera orientation on the arm and demo-object accuracy were not part of this test.

## For subscribers

The publisher binds `tcp://127.0.0.1:5555`; subscribers connect. Each message is one ZMQ frame: the topic, a space, then compact JSON. Two examples, an object during `approach` and the drop station during `carry`:

```
vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{"track_id":7,"label":"cup","source":"yolo","bbox":[0.41,0.3,0.62,0.58],"confidence":0.91},"guidance":{"dx":-0.12,"dy":0.05,"distance_hint":"near","aligned":false},"summary":"Cup near, slightly left.","alert":null}
vision {"t":1794694924.4676,"frame_id":540,"status":"ok","phase":"carry","target":{"track_id":2,"label":"drop station","source":"aruco","bbox":[0.1383,0.2736,0.2367,0.4486],"confidence":0.938},"guidance":{"dx":-0.625,"dy":-0.278,"distance_hint":"far","aligned":false},"summary":"Drop station ahead, left and slightly up.","alert":null}
```

Every message has exactly these eight keys:

| Field | Meaning |
|---|---|
| `t` | Capture time of the frame, Unix seconds. |
| `frame_id` | Frame number from the source. Gaps mean skipped frames. |
| `status` | `ok`: target locked. `low_confidence`: something is visible but not sure enough to lock. `no_target`: nothing that may be the target in this phase is visible. `degraded`: no fresh frames. |
| `phase` | `search`, `approach`, `grasp`, `carry`, `place` or `release`; see "Phases". A `degraded` heartbeat carries the last phase that was published. |
| `target` | `null` unless `status` is `ok`. `label` is a name from `detector.classes`, a name the operator taught (see "Teaching vision a name"), or `drop station`. `source` is `yolo` for objects and `aruco` for the station marker. `bbox` is `[x1, y1, x2, y2]`, each 0 to 1, always with `x1 < x2` and `y1 < y2`. `confidence` is 0 to 1: the detector's score for an object, OpenCV's rating of the read for a marker. |
| `guidance` | `null` unless `status` is `ok`. `dx`, `dy` are −1 to 1, target centre minus frame centre; positive is right and down in gripper terms. `distance_hint` is `far`, `near` or `in_reach`. `aligned` is true inside the deadzone. |
| `summary` | A sentence to speak, or `null` when there is nothing new to say. Vision's answers to the label commands ("Learning my flask.", "My flask is ready.") arrive here too, with the next frame's message. An answer whose next frame comes more than `watchdog.stall_timeout_s` (0.5 s) later (the feed stalled or was paused) is only logged, not sent. |
| `alert` | `null`, or `"dropped"` for `phase.alert_hold_s` (1.0 s) after the object was let go during `carry`; see "The `dropped` alert". Heartbeats always carry `null`. |

Three rules for every consumer:

- **Act on guidance only when `status == "ok"`.** Everything else means no guidance.
- **Treat silence, or a `t` older than 0.5 s, as no guidance.** The watchdog publishes `degraded` every 0.5 s while the feed is stalled (the first one says "Vision feed lost."), but it cannot report its own process dying.
- **Read `alert` on every message, whatever the status.** A drop can come with any status.

A minimal subscriber (haptics wants `CONFLATE`, which keeps only the newest message; a logger does not):

```python
import json, time, zmq

sock = zmq.Context.instance().socket(zmq.SUB)
sock.set(zmq.CONFLATE, 1)                 # before connect
sock.connect("tcp://127.0.0.1:5555")
sock.subscribe(b"vision ")
while True:
    if not sock.poll(500):                # silence
        stop_guidance(); continue
    msg = json.loads(sock.recv().partition(b" ")[2])
    if msg["alert"] == "dropped":         # repeated for 1 s, so it arrives on many messages in a row
        drop_alarm()
    if msg["status"] != "ok" or time.time() - msg["t"] > 0.5:
        stop_guidance(); continue
    pulse(msg["guidance"]["dx"], msg["guidance"]["dy"], msg["guidance"]["aligned"])
```

`paul_vision/subscribe_print.py` is the reference implementation, and `paul_vision.messages.validate_vision` checks a message against the contract (it rejects a missing or extra key).

## Phases

Every message carries the task phase. The phase also decides what may be the target: objects in `search`, `approach`, `grasp` and `release`; only the drop station's marker in `carry` and `place`. The pick station's marker is never a target.

| Phase | What it means | It moves on when |
|---|---|---|
| `search` | No object is locked. | An object is locked: `approach`. The pads report contact: `grasp`. |
| `approach` | An object is locked (or has just disappeared at grasp range: see "Blind spot" below). | The lock is lost: `search`. Contact: `grasp`. |
| `grasp` | The pads report contact; the gripper is closing on the object. | Contact held for `phase.grasp_settle_s` (0.5 s): `carry`, with "Grip established.". Contact off first: back to search/approach, with no alert (a failed grasp). |
| `carry` | Holding the object. The target is the drop station; objects are ignored, so a cup in view gives `no_target`. | The drop station at `phase.place_hint` (`near`) or closer for `phase.place_dwell_s` (0.5 s): `place`. Contact off: search/approach, with `alert: "dropped"` and "Grip lost. Object may have dropped.". |
| `place` | At the drop station. Losing sight of the marker keeps `place`. | Contact off: `release`, with "Released.". The station seen at `phase.leave_place_hint` (`far`) for `phase.leave_place_dwell_s` (1.0 s), after it was seen at `near`: back to `carry`. |
| `release` | Let go at the station. | After `phase.release_hold_s` (1.0 s): search/approach. Contact again: `grasp`. |

"Search/approach" means whichever applies: `approach` while an object is locked, `search` otherwise, exactly as in milestone 1. Contact is decided from the pads (see "Gripper pressure"), or with `gripper_load.enabled` from the gripper servo's load (see "Gripper load"); "pad data" below means whichever of the two is on.

- **Without pad data** (no mock or bridge running; with the gripper load, no `robot` message with a measured load) and without `set_phase`, only `search` and `approach` occur. The banner's `pressure:` or `grip:` line says so.
- **Blind spot.** An object that fills the frame stops being detected (see "What the sample clips showed"), so the target disappears just as the gripper closes. If the locked object was at `phase.blind_spot_hint` (`in_reach`) when it disappeared, and pad readings are arriving, the phase stays `approach` for up to `phase.blind_spot_s` (1.5 s) and "Target lost." is not said. Without pad data the blind spot is off; `blind_spot_s: 0` turns it off.
- **Pad data stops** during a grasp, carry or place that the pads started: the phase is kept for `phase.sensor_loss_hold_s` (2.0 s), long enough for the bridge to reopen its port, then goes back to search/approach with "Grip sensor lost." and no alert. Missing or stale pad data is never a drop.
- **`set_phase`** (see "Commands") forces a phase from the next frame. A forced `grasp`, `carry` or `place` is kept without pad data. The pad state at the moment of the command does not count as a change: `set_phase search` while the pads read contact stays in search until contact goes off and on again. If the pads are undecided, stale or silent when the command is applied, the first state they decide afterwards is taken as that state: it does not count as a change either, so contact decided then does not start a grasp. A forced `place` goes back to `carry` only after the station has been seen at `near`.
- **A camera switch** from the console starts again at `search`, also in the middle of a `carry`, and ends a `dropped` alert being held. With the pads still reading contact, the next frame starts a new `grasp`, and `carry` comes back after `phase.grasp_settle_s` with "Grip established." again. A stall longer than `watchdog.stall_timeout_s` (0.5 s) resets tracking but keeps the phase: a camera hiccup is not a drop.
- **With `stations.enabled: false`** the drop station is never seen, so `carry` never reaches `place` by itself (the banner says so); `set_phase place` still works.

What is said depends on the phase. In `search` and `approach`: the target sentence, else "Target lost." or "No target." (nothing over the blind spot). In `carry`: the drop station sentence ("Drop station ahead, left and slightly up."), else "Looking for the drop station.". In `place`: the drop station sentence, else nothing. In `grasp` and `release`: the target sentence, else nothing. The four event sentences ("Grip established.", "Released.", "Grip lost. Object may have dropped.", "Grip sensor lost.") are said on the frame they happen, without waiting for `summary.settle_s` or `summary.min_interval_s`. So are vision's answers to the label commands ("Learning my flask.", "My flask is ready."), after any event sentence of the same frame, in one `summary`. An answer does not count as the scene's sentence: a scene sentence that is due still comes, at least `summary.min_interval_s` later.

### The `dropped` alert

`alert` becomes `"dropped"` when the pads go from contact to no contact during `carry`, that is, away from the drop station. Letting go in `place` is a release and letting go in `grasp` is a failed grasp; neither raises it. It needs contact during the current hold and then fresh readings of no contact (at or below `pressure.off_threshold` for `pressure.off_hold_s`, or the same keys under `gripper_load:`); a pad feed that goes silent gives "Grip sensor lost." instead.

The alert is repeated on every message for `phase.alert_hold_s` (1.0 s), so that a subscriber that keeps only the newest message cannot miss it. The phase goes back to search/approach at once. On a bench run (mock pads, a recorded clip) it lasted 1.0 s, with "Grip lost. Object may have dropped." on its first message. Through the launcher (mock pads, the built-in camera at about 15 FPS) it reached the bridge's WebSocket on 15 messages over 0.95 s. The web console raises "Object may have dropped" for it (see "Web console"); no haptics act on it yet (see `NOT_BUILT.md`).

## Commands

Vision reads the `command` topic from every endpoint in `transport.command_sub`. Only one process can bind a port, so each sender binds its own and vision connects to all of them:

| Endpoint | Sender |
|---|---|
| `tcp://127.0.0.1:5557` | the console bridge (`../bridge`) |
| `tcp://127.0.0.1:5559` | the voice process, `python -m paul_vision.voice` (`voice.endpoint`; see "Voice commands") |
| `tcp://127.0.0.1:5560` | `python -m paul_vision.send_command` (the last entry is its default) |

| Command | Payload | Effect |
|---|---|---|
| `select_target` | `{"t": 1731542400.2, "type": "select_target", "label": "pill bottle"}` | Only objects with this label can be the target. The label must be one of `detector.classes` or a name that has been taught and is live now (see "Teaching vision a name"); anything else is logged and ignored. |
| `clear_target` | `{"t": 1731542400.2, "type": "clear_target"}` | Any object can be the target again. |
| `set_phase` | `{"t": 1731542400.2, "type": "set_phase", "phase": "carry"}` | Forces the phase from the next frame (see "Phases"). The phase must be one of the six; anything else is logged and ignored. |
| `tag_visual` | `{"t": 1731542400.2, "type": "tag_visual", "name": "my flask", "bbox": [0.31, 0.22, 0.45, 0.61]}` | Learns what is in the box as `name`. `bbox` is `[x1, y1, x2, y2]`, each 0 to 1 of the picture; `null` means the locked target's box. |
| `tag_text` | `{"t": 1731542400.2, "type": "tag_text", "name": "my mug", "prompt": "white mug"}` | Learns to look for the description `prompt` as `name`. Without `prompt`, the name itself is the description. |
| `untag` | `{"t": 1731542400.2, "type": "untag", "name": "my flask"}` | Forgets everything taught as `name`. |

The three label commands are not judged on arrival like the others: vision accepts or refuses each one and says which in `summary`, as described under "Teaching vision a name". A name is stored in lower case with single spaces.

On the wire: `command {"t":1731542400.2,"type":"set_phase","phase":"carry"}`. Commands of other types (the console's `set_haptics`) are ignored. A command sent before vision has connected is lost.

To send one by hand:

```powershell
uv run python -m paul_vision.send_command set_phase carry
uv run python -m paul_vision.send_command select_target pill bottle     # quotes around the label are optional
uv run python -m paul_vision.send_command clear_target
uv run python -m paul_vision.send_command tag_visual my flask           # learn the locked target as "my flask"
uv run python -m paul_vision.send_command tag_visual my flask --bbox 0.31,0.22,0.45,0.61
uv run python -m paul_vision.send_command tag_text my mug --prompt "white mug"
uv run python -m paul_vision.send_command select_target my flask        # once vision has said "My flask is ready."
uv run python -m paul_vision.send_command untag my flask
```

It binds the last entry of `transport.command_sub`, waits up to `--wait` seconds (default 3) until vision has subscribed, sends the command once, prints for example `sent {"t":1794694736.056261,"type":"set_phase","phase":"carry"} to 1 subscriber on tcp://127.0.0.1:5560` and exits. Before sending it checks a phase, and a `select_target` label against `detector.classes` and the names in the label library, as vision will (if the library cannot be read it says so and checks `detector.classes` only). `--bbox` must be four numbers 0 to 1 with x1 < x2 and y1 < y2, and is only for `tag_visual`; `--prompt` is only for `tag_text`. Whether a tag command can be done is left to vision, which answers by voice. Exit status: 0 sent; 1 not sent (the port is taken, or nobody subscribed in time); 2 bad arguments, an unknown phase or label, or a configuration that cannot be loaded (one line on stderr starting `send_command: configuration:`). `--endpoint` binds another endpoint; it must be listed in `transport.command_sub` or vision never hears it (send_command warns). `uv run python -m paul_vision.subscribe_print --topic command` shows every command from every sender.

## Teaching vision a name

The operator can teach vision a name for one particular object while it runs, for example "my flask" for one flask among other bottles, and then select it like a class. A taught name is a **tag**. Each tag has one or more **views**, at most `labels.max_views` (5): a box on a camera picture (`tag_visual`) or a written description (`tag_text`). Every view becomes one more class inside the detector, called `my flask#0`, `my flask#1` and so on, and is published as `my flask` alone. A tag may have the same name as one of `detector.classes`: its views then add to that class, and `untag` removes only those views. The library holds at most `labels.max_tags` (20) names.

A change goes like this:

1. Vision checks the command against the newest frame and the library, and answers at once in `summary`: "Learning my flask." or the reason it will not (table below). Every answer goes out with the next frame's message; if that frame comes more than `watchdog.stall_timeout_s` (0.5 s) later, the answer is only logged.
2. A separate process, the label worker (`python -m paul_vision.label_worker`), makes the new view's embedding with PyTorch on the CPU, whatever the detector profile, at low priority and with `labels.worker_threads` (2) threads. On the OpenVINO profile it also builds a new export, because an export has its class list built in, and vision then loads and compiles that export on a thread. The old class list keeps serving frames all the while. The heavy work is in another process because building or exporting a model inside vision stalled its frame loop for 557 to 816 ms.
3. Between two frames the new class list takes over, `labels/library.json` is updated, and vision says "My flask is ready." From that frame on, `select_target my flask` is accepted and `target.label` can be `my flask`. "Ready" means that the model containing the name serves frames, not that the object has been found under that name yet.

Only one change runs at a time. A worker that fails changes nothing ("Could not learn my flask."; the log says why), and one that runs longer than `labels.worker_timeout_s` (120 s) is stopped. On OpenVINO, loading that takes longer than `labels.load_timeout_s` (90 s) is reported as failed; the loading thread cannot be stopped, so label commands are refused as busy until it ends. A change that has not gone live when vision stops is dropped. A change that went live but could not be saved is saved before the next change starts, and when vision stops, if `library.json` can be replaced then.

When a tag goes live or is removed, every tracked object takes the label of its next detection (the tracker's label votes are cleared). If the operator had narrowed the target to a label and the locked target turns out to be the new name, the narrowing moves to the new name, so teaching the object in front of the gripper does not lose it. `untag` of the name the target is narrowed to clears the narrowing (unless the name is also one of `detector.classes`): any object can be the target again.

Which box `tag_visual` learns from:

- **No box** (`"bbox": null`, `send_command tag_visual <name>` without `--bbox`, or "this is ..." by voice): the locked target's box on the newest processed frame. The target must be an object, not a station. If the target was missed in the newest frame and is coasting, the newest frame in which the same track was detected is used, if it is no older than `watchdog.stall_timeout_s` (0.5 s).
- **A given box**: if it overlaps a tracked object with an IoU of at least `labels.snap_iou` (0.5), that object's own box is used instead. It is tighter, and a loose box teaches the background: a box 50 % too large matched nothing in tests.
- Either way the picture must be no older than `watchdog.stall_timeout_s`, the box must keep `labels.border_margin` (0.01 of the frame) away from every edge (a cut-off view matched nothing in tests), and each side must be at least `labels.min_box_side` (0.04) of the frame's side.

`tag_text` learns from the words of `prompt` (the name if there is none), through the text encoder that `bake` put in `models/`.

What vision says, with the name in place of X ("X is ready." starts with a capital):

| Sentence | When |
|---|---|
| "Learning X." / "Forgetting X." | Accepted; the worker has started. |
| "X is ready." | The tag is live. |
| "Forgot X." / "Forgot what was taught for X." | The tag is gone. The second when X is also one of `detector.classes`, which stays. |
| "..., but it could not be saved." | Replaces the full stop of the two rows above when `library.json` could not be replaced: the change is live, and `library.next.json` keeps it. Vision saves it before the next change starts and when it stops; the change is gone after a restart only if all of those saves failed. |
| "Cannot learn X: the change to Y could not be saved yet." / "Cannot forget X: the change to Y could not be saved yet." | The change to Y is live but not saved, and `library.json` still cannot be replaced. No change starts until it can, because every worker starts from `library.json`. |
| "Busy learning X. Try again in a moment." / "Busy forgetting X. Try again in a moment." | Another change is still running. |
| "Nothing was learned as X." | `untag` of a name that passes the name rules (next row) but is not a tag. |
| "The name is empty." / "That name is too long." / "Names cannot contain a slash or a hash sign." / "That name has characters that cannot be used." / "That name belongs to a station." | The name cannot be a tag. Names are at most `labels.max_name_chars` (40) characters; `pick station` and `drop station` are taken. The name is checked before anything else, for `untag` too and even while another change runs, so a name that breaks the rules is never repeated aloud; only "The name is empty." comes after the busy answer. |
| "The library is full (20 names)." / "X already has 5 views." | `labels.max_tags` or `labels.max_views` reached. |
| "Cannot learn X: the description is empty." / "Cannot learn X: the description is too long." | `tag_text` with an empty `prompt`, or one longer than `labels.max_prompt_chars` (100). |
| "Cannot learn X: there is no current picture." | No frame within `watchdog.stall_timeout_s`. |
| "Cannot learn X: nothing is locked." / "... the target is a station." / "... the target is out of view." | `tag_visual` without a box. |
| "Cannot learn X: the box is not valid." / "... it touches the edge of the picture." / "... the box is too small." | `tag_visual` with a box, or the target's box. |
| "Could not learn X." / "Could not forget X." | The worker could not be started, the worker or the loading failed, or an unexpected error ended the change (it is logged with its traceback); nothing changed. "that" stands in for a name that is empty or breaks the name rules. |
| "Learning X took too long." / "Forgetting X took too long." | `labels.worker_timeout_s` passed. |

The log has one line per change, for example `label change tag_visual 'my flask' live after 15.7 s (worker ..., load ..., swap ... ms); classes [...]`, and the worker's own output is in `labels/jobs/` (the last 10 runs).

### What has been run

On 14 November 2026, on the development laptop, on loops cut from `recordings/run1.mp4` (not the wrist camera; nothing of this has run on the CUDA machine):

| | OpenVINO on the Intel GPU | PyTorch on the CPU (about 9 FPS) |
|---|---|---|
| `tag_visual`, command to "ready" | 15.7 s for "my flask" with a box, snapped at IoU 0.66 (worker 13.8 s, loading 1.8 s, switch-over 0.1 ms) | 7.5 s without a box (worker 7.2 s: starting the process 4.2 s, loading the model 1.0 s, the embedding 0.6 s); 7.75 s with a box, snapped at IoU 0.82 |
| `tag_text`, command to "ready" | 24.8 s for "my can" from "black energy drink can" (worker 13.0 s, loading 11.7 s) | 11.0 s for "my mug" from "white mug", sent with `send_command` |
| `untag`, command to "Forgot ..." | 3.9 s and 9.9 s | not measured |
| Longest gap between messages during a change | 52 to 112 ms, never `degraded` | 200 ms, never `degraded` |
| Frame rate and detector time | 29.3 to 30.3 FPS, 27.0 to 28.6 FPS while the worker ran; the `yolo` stage 20.2 to 21.8 ms without tags, 21.0 to 22.2 ms with one | median detection 88.0 ms with no view, 87.1 ms with one, 91.9 ms with five |

- After `select_target my flask`, the target was "my flask" in 637 messages on OpenVINO and 2117 on PyTorch, and never had its centre right of x = 0.55, where a water bottle stands. That is one clip with one look-alike, not proof that a picture tells similar objects apart in general.
- After `untag my flask`, the locked object stayed the target, relabelled "bottle", without "Target lost.".
- A second command during a change was refused as busy; the edge, out-of-view, nothing-locked (after `set_phase carry`) and bad-name refusals and "Nothing was learned as water bottle." were heard as intended. `labels.min_box_side` refused the far water bottle (0.033 of the frame wide).
- `scripts/eval_clips.py` on two sample clips gave the same results as before tags existed.
- Later, single runs on OpenVINO on the Intel GPU with a looped still picture, a scratch library and nothing else running: "My flask is ready." 13.9 s after `tag_visual` with a box (an earlier run: 9.2 s); "this is my flask" by voice, a second view from the locked target, 15.2 s (earlier 12.5 s); "Forgot my flask." 8.4 s after "forget my flask" (earlier 7.8 to 10.9 s). In the last run the first message came 7.7 s after launch, and 1984 messages in 66.8 s (29.7 FPS) all had status `ok` and exactly the eight keys, with no gap longer than 87 ms.
- Live through the launcher (`../demo.ps1 -ServicesOnly`, see "The team launcher") that evening, on OpenVINO on the Intel GPU and the laptop's built-in camera at about 15 FPS (median 14.8; `detect` median 51 ms): `tag_text` "flask" (the description "metal water flask") was live 47.5 s after the command, and after a restart the banner listed it (`labels: 1 tag(s) ['flask'] in ...\computer_vision\labels`). Then "mug" from a typed voice line, live after 48.7 s, and forgotten after 39.6 s; "thermos" from a WebSocket client sending what the console sends, 40.9 s, forgotten after 29.7 s; and a box drawn on the console's camera view, learned as "face", 33.5 s. A box under `labels.min_box_side` and the name "pick station" were refused with their sentences. Of each worker's run, starting the process took 7.9 to 10.9 s, loading the model 2.6 to 4.8 s, a text embedding 11.1 to 14.0 s (a picture 1.7 s) and the OpenVINO export 12.4 to 17.0 s. Why these changes took about twice as long as on the clips was not established; the camera, the bridge and the console ran beside them.

How well a text tag finds its object, a stale library rebuilt at startup, the two timeouts, teaching from a coasting target's last sighting, saving a change again after `library.json` could not be replaced, and ending a change after an unexpected error have run only in the unit tests (see `NOT_BUILT.md`).

### The library on disk

Everything is under `labels.dir` (`labels/` next to `config.yaml`; not in git, because it holds camera pictures): `library.json` (the names and views), `views-<hash>.npz` (one 512-number embedding per view), `refs/<view id>.png` (the picture each visual view was taken from), `openvino/<key>_openvino_model/` (the export for the current class list), and `jobs/` (worker logs). `library.json` holds what vision detects now: the worker writes a change to `library.next.json`, which becomes `library.json` only once the change is live. If that rename fails (Windows refuses it while another process has `library.json` open, after 5 tries 0.01 s apart), `library.next.json` holds the live library until vision saves it; a start discards `library.next.json` and uses `library.json`.

At startup, with tags in the library:

- A library made with other weights or another `detector.imgsz` is rebuilt from its saved pictures and prompts before the first frame.
- On OpenVINO, a missing export for the tags is built before the first frame. Measured: first message 10.5 s after start with the export to build, 5.0 s with it present.
- If either fails, startup stops with `the label library in ... could not be made ready: ...` and the three ways out: `labels rebuild`, `labels remove <name>`, or moving the folder away to start without tags. A `library.json` that cannot be read stops startup too (see "Troubleshooting").

### Offline commands

While vision is stopped:

```powershell
uv run python -m paul_vision.labels list                      # the tags, their views, and what they were made with
uv run python -m paul_vision.labels remove my flask           # forget a tag; the words are joined into one name
uv run python -m paul_vision.labels rebuild                   # every embedding again from the saved pictures and prompts
uv run python -m paul_vision.labels rebuild --backend torch   # without the OpenVINO export
```

`rebuild` uses `detector.backend` by default, with `auto` counting as `openvino`, which also builds the export (7.8 s on the laptop). `remove` does not check whether vision is running: while it runs, send `untag` instead, or the running vision and the library disagree. Exit status: 0 done; 1 `remove` of a name that is not a tag; 2 bad arguments, a configuration that cannot be loaded (one line on stderr starting `configuration:`), or the library or the worker failed (the message says which).

## Voice commands

`python -m paul_vision.voice` is a separate process that turns spoken commands, as text, into the commands above. It does not listen by itself: a speech recognizer writes one utterance per line into it, or it runs the stand-in recognizer described below (`--listen`). It binds `voice.endpoint` (`tcp://127.0.0.1:5559`, one of `transport.command_sub`) and vision connects to it, so the two start in any order. It plays no audio and sends nothing anywhere but to vision on this computer; what it understood is printed, not spoken.

```powershell
uv run python -m paul_vision.voice                         # one utterance per line on stdin, each starting with a wake word
uv run python -m paul_vision.voice --no-wake-word          # typing at the keyboard: every line counts
uv run python -m paul_vision.voice --say "find the cup"    # one utterance, no wake word needed, then exit
uv run python -m paul_vision.voice --listen                # the stand-in recognizer on the default microphone; typed lines work too
uv run python -m paul_vision.voice --grammar               # every phrase it understands
```

Any recognizer that writes one utterance per line (UTF-8) can be piped into the first form. An utterance counts only if it starts with a wake word from `voice.wake_words`: "paul", "hey paul", "ok paul" or "okay paul"; the others are printed as ignored. `--no-wake-word` lifts that for typed and piped lines, also those typed under `--listen` (a leading wake word is still removed); `--say` never needs one, and speech heard by `--listen` always does. An empty `voice.wake_words` makes every utterance count. After the wake word, a leading "please" and a trailing "please", "thanks" or "thank you" are dropped.

| Said after the wake word | Sent |
|---|---|
| "find the cup"; also select, look for, search for, target, track, go for, pick up, grab, get, fetch | `select_target` with `label` `cup` |
| "any object", "anything", "clear", "never mind", "cancel", "no target", "find anything", ... | `clear_target` |
| "phase carry", "set the phase to carry", "carry phase", "switch to carry phase" | `set_phase` with `phase` `carry` |
| "this is my flask"; also call this, name it, remember this as, label this, learn this as, ... | `tag_visual` with `name` `my flask` and `bbox` `null`: vision learns from its locked target |
| "learn red mug"; also learn to find, learn to recognize, also look for, also find, also search for, also track | `tag_text` with `name` and `prompt` both `red mug` |
| "forget my flask"; also forget about, unlearn, remove the tag, delete the tag | `untag` with `name` `my flask` |

Every payload also carries `t`, and `--grammar` lists every phrase. A label or a phase said alone ("paul cup") is not a command. By voice, a tag is always taught from the locked target and a description is always the name itself; a box or another description needs `send_command`.

**It never guesses.** The labels it knows are `detector.classes` plus the tags in `labels/library.json`, read again for every utterance, so a new tag counts once vision has said it is ready. A tag name in the library that voice cannot use is left out with a warning (see "Troubleshooting"). Labels are compared as they are said, which is their words without punctuation: "mug (red)" is said "mug red". Two labels said the same way ("mug (red)" and "mug red") are refused, not chosen between. A spoken label is matched by the first of these rules that names exactly one label:

1. the words as said ("my flask");
2. without a leading "the", "a", "an", "my", "our", "your", "this", "that" or "some" ("the cup" is `cup`);
3. with a plain plural made singular ("cups", "boxes");
4. otherwise by spelling similarity (Python's `difflib`): the best label must score at least `voice.label_cutoff` (0.9) and lead the next one by `voice.label_margin` (0.1). Only a label the words could be a mishearing of counts: the numbers in it must be the same ("station 13" scores 0.90 against "station 12" and is refused); with as many words on both sides, each word must be the same, a plural of it, or itself at least `voice.label_cutoff` similar ("large red jug" scores 0.92 against "large red mug" and is refused); and a word of one letter must be the same ("top shelf c" is not "top shelf b", at 0.91). "pillbottle" is taken as pill bottle (0.95).

If two labels fit equally, or none fits well enough, nothing is sent and the output says why: it names the closest label and its score, and either the score needed or the rule that failed (`closest 'large red mug' scores 0.92, but 'jug' is not 'mug'`). A phase is matched the same way. A new tag name is taken as said, in lower case and without a leading "the", "a" or "an". It is refused if it points instead of naming ("this is it", "this is that one"), if it breaks the name rules under "Teaching vision a name", if it holds a wake word ("this is hey paul cup"), or if it is a command itself ("this is cancel", "learn find the cup"): say the name alone. "forget" needs the exact name of a tag as it is said ("forget mug red" forgets `mug (red)`): for a near miss it asks "did you mean ...?" and sends nothing, and a class without taught views is not forgotten.

For every utterance it prints what it heard, what it understood and what it sent, or why it sent nothing. With the shipped config:

```
> paul find the cup
  understood: select target 'cup'
  sent to 1 subscriber: {"type":"select_target","label":"cup","t":...}
> paul find the banana
  nothing sent: no known label like 'the banana' (closest 'bottle' scores 0.25, needs 0.90); known: cup, bottle, pill bottle, box
> paul cup
  nothing sent: 'cup' alone is not a command; to select it say 'find cup'
> find the cup
  ignored: no wake word (paul, hey paul, ok paul, okay paul)
```

**Sent is not accepted.** With nobody subscribed, it waits `voice.deliver_wait_s` (2 s), then prints `NOT DELIVERED: nobody subscribed on ... within 2 s, so it was dropped (commands are never queued)`; say it again once vision runs. "sent to N subscribers" counts every subscriber to `command`, a `subscribe_print` included: the command left, but vision may still refuse a label command. Vision says so in `summary` ("Cannot learn my cup: nothing is locked.") and in its log. "this is ..." needs a target that vision sees in the newest frame, so it sometimes has to be said again.

Exit status: 0 at the end of input (Ctrl+Z then Enter at the keyboard) or on Ctrl+C, and for `--say` when the command was sent; 1 when `--say` sent nothing, `voice.endpoint` is taken, or the stand-in recognizer could not start or stopped; 2 bad arguments, or a configuration that cannot be loaded (one line on stderr starting `voice: configuration:`). Starting took about 0.3 s on the idle laptop (0.36 to 0.42 s through `uv run`) and 0.85 to 0.89 s with the CPU 74 % busy.

### The stand-in recognizer (`--listen`)

The team's speech recognizer has not been chosen. So that the demo can be driven by voice without a network, `--listen` starts Windows' own offline recognizer (System.Speech) as a child process, with this command line:

```
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <this folder>\scripts\voice_listen.ps1
```

`-ExecutionPolicy Bypass` lets that one PowerShell process run the unsigned script whatever the machine's policy is; it changes no setting. It needs Windows PowerShell 5.1 (`powershell.exe`, part of Windows), not PowerShell 7. It listens to Windows' default recording device, in the language of `voice.listen.culture` (`en-US`).

The recognizer can only hear the phrases of a grammar that voice builds from the command phrases and the known labels, each after a wake word: a select verb with a label or with "anything" / "any object", the clear phrases, the phase phrases, and "forget" with an existing tag. "target" and "get" are left out of the spoken select verbs, because in the loopback test below "forget my flask" was heard as "target my flask". The one exception to "only commands" is `voice.listen.decoy_words` (`[bowl, paula]`): words that sound like a wake word or a label, offered beside the wake words and beside the labels after a select verb, so that "paula" or "find the bowl" can be heard as itself instead of as "paul" or "find the bottle"; voice sends nothing for them. A decoy word said like a wake word or a known label is left out. Every `voice.listen.labels_refresh_s` (2 s) voice checks the tags, and when they changed it stops the recognizer, starts a new one with the new grammar, and prints `voice: the tags changed (...); grammar rebuilt`; speech in that time, about 1 s, is not heard.

New tag names are not spoken by default: "this is ..." and "learn ..." are in the grammar only with `voice.listen.dictated_names: true`, because free dictation got names wrong at high confidence (10 of 12 right; "red mug" came back as "red dog" at 0.95 and "red mount" at 0.97). Type them into the same window instead, since typed lines keep working under `--listen`; "find" and "forget" with an existing tag work by voice.

Each recognition is printed with its confidence. It is not sent when the confidence is below `voice.listen.min_confidence` (0.70), or when it landed on the decoy: with `voice.listen.decoy` on, free dictation is loaded beside the commands, so speech that is not a command lands there instead of on the closest-sounding command. Speaker adaptation is off (`voice.listen.adaptation: false`). Once the grammar is loaded it prints `voice: listening (recognizer ready N ms after start, ...)`; on the laptop that took 437 to 844 ms (844 on a cold first run).

**It has been measured by loopback only, never with a microphone.** `scripts/voice_loopback.py` has Windows' speech synthesizer speak phrases into wav files, the stand-in hears them through the same code as `--listen`, and each phrase is scored right, missed, or WRONG (a command would have been sent that was not meant). On 14 November 2026 on the laptop, with the shipped settings:

- Clean speech, two synthetic voices at speaking rates −2, 0 and +3, 234 phrases: of the 132 commands, 130 were right and 2 missed, and none was taken for another command; of the 102 phrases that are not commands, 7 were sent as one (13 without `decoy_words`). All 7 had a word that sounds like a known one: "cap" taken as cup (4) and "paula" as paul (3), at confidence 0.85 to 0.97. The decoy words took "bowl" as bottle from 3 to 0 and "paula" as paul from 6 to 3. A `min_confidence` of 0.9 gave 5 wrong and 9 missed, 0.8 gave 7 wrong and 6 missed: no threshold removes the sound-alikes. At one speaking rate, 86 phrases: 82 right, 2 missed, 2 wrong (79, 2 and 5 without `decoy_words`).
- "cap" is not a decoy word: in a second phrase set it turned 6 of 54 correctly spoken "cup" commands into "cap", which sends nothing. That set's sound-alikes that are not decoy words ("cop", "cub", "cab", "books", ...) were still heard as the label: 45 of its 96 phrases that are not commands were sent as one (52 without `decoy_words`).
- White noise 30 dB below the speech: 19 of 44 commands heard (20 without `decoy_words`), none wrong. 20 dB below: none heard, none wrong, measured without `decoy_words` and with `[cap, bowl, paula]`, not with the shipped `[bowl, paula]`. Without the decoy, 20 dB gave 13 wrong in 78 phrases, at confidence up to 0.95.
- With speaker adaptation on, the second voice missed 4 to 16 of 39 commands per speaking rate.

```powershell
uv run python scripts/voice_loopback.py --work-dir <empty folder>
uv run python scripts/voice_loopback.py --work-dir <empty folder> --rate -2 --rate 0 --rate 3 --snr-db 20 --tag "my flask"
```

`--work-dir` is where the wav files go (without it, a new temporary folder), `--snr-db` adds white noise that many dB below the speech, `--rate` sets the speaking rate (repeat it for several), `--tag` pretends a tag exists, `--decoy`, `--adaptation` and `--names` (`on` or `off`) override the `voice.listen` settings, and `--json FILE` also writes every result. The decoy words are those of `voice.listen.decoy_words` in the config (`--config` for another file). It uses the installed voices "Microsoft David Desktop" and "Microsoft Zira Desktop". This is synthetic speech: it shows whether the grammar and the threshold behave, not how a room, an accent or another voice will sound.

### What has been run

On 14 November 2026 on the laptop, with vision on `sample_videos/wrist_so101_cup.mp4` (looped), `subscribe_print --topic command` beside it, spare ports, and voice fed typed lines, not speech:

- `--say "find the cup"` before vision had started printed NOT DELIVERED and exited 1.
- "paul find the bottle" moved the target from the box to the bottle; after "paul find the cup", cup was the only target label for the next 4 s (20 messages); "paul any object" cleared the choice.
- "paul phase grasp": 89 of the next 90 messages had phase `grasp`. "paul set the phase to search": `search`, then `approach` by itself once the cup locked.
- "paul find the banana" and "find the cup" (no wake word) sent nothing.
- "paul learn red mug" was live after 23.4 s, and "paul find red mug" was then sent as a tag (the clip has no red mug, so `no_target`). "paul forget red mug" took 5.7 s; saying it again was refused by voice.
- "paul this is my cup" was refused by vision once while the lock had dropped ("nothing is locked.") and once while it was coasting ("the target is out of view."). The next try, 1.5 s after the second refusal, used the locked cup's box and was live after 10.1 s. "paul find my cup": `my cup` in 117 of the next 240 messages, and no other label. "paul forget my cup": 7.1 s.

That evening, voice ran with `--no-wake-word` and typed lines beside the launcher's vision, bridge and console (see "The team launcher"), on the laptop's built-in camera:

- "find the flusk" and "find the cop" sent nothing (closest `flask` at 0.80 and `cup` at 0.67, both under 0.90).
- "clear target", "phase carry" and "search phase" took effect, and the console showed the phase.
- "learn mug" was live after 48.7 s, and "forget mug" took 39.6 s.
- `--listen` on the laptop's microphone for 20 s: the recognizer was ready 1641 ms after start, heard three bits of room speech at confidence 0.01 to 0.12 and sent nothing. No command was spoken to it.

Not run: commands spoken to `--listen`, the team's recognizer, and the demo machine (see `NOT_BUILT.md`).

## Web console

The console in `../frontend` shows the camera picture with the guidance drawn over it. It needs vision and the bridge (`../bridge`), each in its own terminal, started in any order:

```powershell
uv run python -m paul_vision --source wrist     # here; --source 0 for a laptop's built-in camera
uv run python -m paul_bridge                    # in ../bridge
npm run dev                                     # in ../frontend; then Signals, "Live bridge", Connect
```

- **Picture.** Vision serves the frames it is processing as MJPEG at `http://127.0.0.1:8766/stream` (`stream:` in `config.yaml`; `--no-stream` turns it off). The console cannot open the camera itself: on Windows a camera held by one program is not available to another. Nothing is encoded while no one is watching.
- **Camera choice.** The same server lists the cameras (`GET /sources`) and switches to another (`POST /source`), for pages served from this computer only. The switch happens between two frames; the phase starts again at `search`. Not available with `--record`.
- **Messages.** The bridge relays the `vision` topic to the console over a WebSocket.
- **Commands.** The console's target buttons send `select_target` and `clear_target`, and its phase control sends `set_phase`, through the bridge, which binds `tcp://127.0.0.1:5557`, the first entry of `transport.command_sub`. The bridge passes on any command with a string `type`, so the label commands need nothing there.
- **Names.** On the Signals page, the "Names" group takes a name and an optional description and sends `tag_visual` for the locked target (`bbox` null), `tag_text`, `untag`, and `select_target` for a tag (the target buttons know only `detector.classes`). Under the camera view, **Draw box** lets the operator drag a box on the picture and sends `tag_visual` with it, as fractions of the picture (the picture fills the camera window and is not mirrored, so the box goes out as drawn). Vision's answers ("Learning ...", "... is ready.", refusals) come back in `summary`, which the console shows.
- **Alert.** `alert: "dropped"` raises "Object may have dropped" on the camera view, held for the console's alert time after the alert ends.

Run on 14 November 2026 on the laptop with its built-in camera (`--source 0`), the bridge, and the console in headless Chrome: the picture and the overlay showed, `select_target` and `clear_target` took effect, and the console recovered by itself when vision was stopped and started again. The wrist camera was not plugged in for that run.

Run that evening through the launcher (see "The team launcher"), on the built-in camera:

- A browser watched the stream for the whole run, and a box drawn on its camera view was learned as "face", live 33.5 s after the command.
- A WebSocket client that sends what the console sends (same JSON, same `Origin`) sent `set_phase carry`, `tag_text` (a description), `select_target` for the new tag, `untag`, `clear_target`, a `tag_visual` box too small, and `tag_text` with the name "pick station"; vision applied or refused each as intended and the bridge logged `command relayed` for every one. Each of the 1510 `vision` messages it received had exactly the eight keys and an `alert` of null or `"dropped"` (the client checked only that, not the console's whole validation).
- With `mock_pressure --script` and `set_phase carry`, a let-go in `carry` brought `alert: "dropped"` to the WebSocket on 15 messages over 0.95 s (478 `vision` and 1471 `pressure` messages, none invalid).

Not checked in a browser: the alert on screen, the "Names" buttons and the phase control (see `NOT_BUILT.md`).

After a change to how the camera server closes a connection, it ran once more on the laptop, with Python's `http.client` and raw sockets instead of a browser: vision on OpenVINO with a looped still clip, the stream on a spare port. One viewer of `/stream` got 65 JPEG frames in 3.0 s. `GET /sources` answered 200 without an `Origin` and with `http://localhost:5173`, and 403 with `https://example.com`. 50 `POST /source` requests whose body came 50 ms after the headers (10 each with a foreign origin, no origin, `text/plain`, a 1025-byte body and an unknown camera id) all got their 403 or 400 with its text. Vision kept publishing, every status `ok`, longest gap 71 ms, and stopped with exit code 0 in 0.8 s. Since that change a browser has watched the stream once, through the launcher (above).

## The team launcher (`../demo.ps1`)

From the repo root, `demo.ps1` starts the bridge, vision and the console, and then the arm controller in the same terminal. It runs `leslider_control/run.py demo` (`leslider_control/session.py`) with Python 3.12 through uv.

```powershell
.\demo.ps1 -Check -ServicesOnly            # checks only: prerequisites and ports; exit code 2 on a problem
.\demo.ps1 -ServicesOnly                   # bridge, vision and console, no arm; Ctrl+C stops what it started
.\demo.ps1                                 # the same, then the arm controller (leslider; -Controller pots)
```

Other parameters: `-LeaderPort` (COM9), `-RobotPort` (COM6), `-CameraSource` (`wrist`; a camera index or a video file), `-VisionConfig` (default `computer_vision/config.yaml`), `-DryRun` (the controller without the robot port), `-NoBrowser`, and `-ControllerOptions` for the controller. Without `-ServicesOnly`, `-Check` also wants both serial ports connected (the robot port not with `-DryRun`) and port 5558 free, so on a laptop without the arm use `-Check -ServicesOnly`.

What it does:

- **Checks** that `computer_vision/.venv`, Node and `frontend/node_modules/vite` exist, and that each of the three service ports is either free or held by a ready service of that kind: the stream on 18766, the bridge's WebSocket on 8765, the console on 5173.
- **Reuses** a service that is already running and ready on its port ("Reusing vision on port 18766; its existing configuration stays active."). A reused vision keeps the configuration it was started with, including its `labels.dir`; stop it for the new copy to apply.
- **Writes a run folder**, `runs/pot-test/<date>-<time>/` under the repo root, with `vision.yaml`, `session.json` (the console URL, the controller, the dry-run flag and the serial ports) and one log per service it starts (`bridge.log`, `vision.log`, `frontend.log`).
- **`vision.yaml`** is a copy of the vision config with `detector.models_dir` and `labels.dir` made absolute against the source config's folder, so the copy uses `computer_vision/models` and `computer_vision/labels`, and tags taught in one session are there in the next. The stream is turned on, on 127.0.0.1 port 18766 (`--stream-port`), because 8766 was taken on the machine the launcher was written on. Nothing else changes.
- **Starts** the bridge (`--commands` on the first `transport.command_sub` entry; it relays `vision`, `pressure` and the controller's `robot` on 5558), then vision with `--source <CameraSource> --no-overlay`, then the console with Vite. Each must be ready within 90 s. The console URL is printed and opened: `http://127.0.0.1:5173/?source=live&wsUrl=ws://127.0.0.1:8765&cameraUrl=http://127.0.0.1:18766/stream#/`.
- **Without `-ServicesOnly`**, runs the arm controller in the foreground, paused, with telemetry on `tcp://127.0.0.1:5558` (the `robot` topic). The terminal takes E (enable or pause), T (torque off) and Q (quit). The launcher's `--robot-pub-port` changes where the controller publishes, but not `transport.robot_sub` in `vision.yaml`; with another port, the gripper load (see "Gripper load") hears nothing.

`--source wrist` opens the camera at `camera.wrist_index` (0), whatever camera that is. On a laptop without the wrist camera that is the built-in one, and vision's log says only `source: camera 0`.

Run on 14 November 2026 in the evening on the laptop, with `-ServicesOnly -NoBrowser` (OpenVINO on the Intel GPU, the built-in camera as `wrist`):

- The labels fix: a session taught "flask" by description (live 47.5 s after the command); after Ctrl+C and a new start, the next banner listed `labels: 1 tag(s) ['flask'] in C:\Users\david\HackPrinceton26\computer_vision\labels`. Before the fix every session started with an empty library in its own run folder.
- The second session (`20261114-220836-003931`): over its first 21 minutes, vision's 635 two-second reports had a median of 14.8 FPS (4.6 to 17.6), `detect` 51 ms and a mean capture-to-publish latency of 52 ms (the worst report: 240 ms mean, 510 ms p95). Which work caused the slow reports was not established. The label changes, the voice process, the console commands and the drop alert of that session are under "Teaching vision a name", "Voice commands" and "Web console".
- The full launcher with the arm controller was not run, so no `robot` message reached vision.

## A bench run without hardware

The whole milestone 2 loop runs on one laptop, without the arm, the pads or the wrist camera. Four terminals in this folder, started in any order:

```powershell
uv run python -m paul_vision --source 0                    # 1. vision (built-in camera, or a video file)
uv run python -m paul_vision.subscribe_print               # 2. what subscribers receive: status, phase, alert, summary
uv run python -m paul_vision.mock_pressure                 # 3. stand-in for the pads, driven by keys
uv run python -m paul_vision.send_command set_phase carry  # 4. one command, whenever needed
```

In the mock's terminal, space toggles contact between 0 and `pressure.mock.contact_level` (0.6), `0` to `9` set the level to 0.0 to 0.9, and `q` or `Esc` quits (keys work in a Windows console only). A level change ramps over `pressure.mock.ramp_s` (0.2 s), as a real grip does. All four pads get the same level.

A walk through every phase: lock a cup (`approach`), press space (`grasp`, then `carry` after 0.5 s with "Grip established."), show the drop station marker (see "Station markers") until it reads `near` for 0.5 s (`place`), press space again (`release` with "Released.", then `search` or `approach` after 1 s). Pressing space during `carry` instead gives `alert: "dropped"` for 1 s. Without a marker to show, `send_command set_phase place` takes its place.

The mock has two more modes:

```powershell
uv run python -m paul_vision.mock_pressure --script "0:0,1:0.6,8:0.6,8.2:0,12:0"   # time:level steps in seconds
uv run python -m paul_vision.mock_pressure --replay pads.log --loop                # a log, with its original timing
```

A script ends when its last ramp has finished, and the pads then go silent, which vision reads as lost pad data, not as an open gripper: end a script with a step that holds the last level for a few seconds (`12:0` above). `--replay` reads a log written with `--log FILE` (by the mock or by the bridge) or saved from `subscribe_print --topic pressure --raw`. Other options: `--duration` (stop after that many seconds, also in the middle of a pause in a replayed log), `--rate`, `--endpoint`, `--log`, `--config`. Exit status 2 means bad arguments or config, 1 that the endpoint is taken (another mock or the bridge is running).

`subscribe_print` takes `--topic vision|pressure|command|robot` (it connects where each is published; `robot` is the arm controller's, see "Gripper load"), `--raw` for the JSON as received, `--conflate`, `--count N` and `--timeout S`.

Run on 14 November 2026 on the laptop, with vision on a synthetic 1280x720 clip of the drop station marker (not in the repo), the mock's `--script` and `send_command`: every message had the eight keys and a valid box; the phases went approach, grasp, carry, place, and back to carry when the marker read `far`; letting go during `carry` gave `dropped` for 1.0 s; and `set_phase carry` without pad data reached `place` on the marker.

A later run on the laptop walked every phase through the real processes, with vision on OpenVINO with a looped still clip, `mock_pressure --script "0:0,6:0.6,12:0.6,12.2:0,16:0,16.2:0.6,26:0.6,26.2:0,32:0"` and `send_command set_phase place`. There were no real pads, arm or station marker, so `place` came from `set_phase`. The phases went approach, grasp, then carry with "Grip established."; letting go in `carry` gave `alert: "dropped"` with "Grip lost. Object may have dropped." for 1.00 s, then approach; gripping again gave grasp and carry; `set_phase place` gave place; letting go gave `release` with "Released.", then approach 1.0 s later. 1169 messages at 29.9 FPS, longest gap 88 ms, never `degraded`. That was one run.

## Station markers

Two printed ArUco markers mark the stations: `pick station` (id 9) and `drop station` (id 15), from `DICT_4X4_50`. Vision looks for them on every frame beside the objects (`paul_vision/aruco.py`) and reports them with `source: "aruco"`. Only the drop station is ever a target, in `carry` and `place`. The pick station is drawn in the overlay and otherwise unused so far. A station's label is the id it decoded, not a guess, so each station keeps its own track: a marker continues only a track of the same station, and its track is never renamed (`IDENTITY_SOURCES` in `tracker.py`). The two stations cannot swap track numbers when they pass each other in the picture, and no object takes over a station's track.

### Printing

```powershell
uv run python scripts/make_markers.py                  # markers/pick_station.png, markers/drop_station.png
uv run python scripts/make_markers.py --size-mm 150    # bigger, for a camera that sees the table from further
```

Each PNG is a 167 x 229 mm sheet at 300 dpi (fits A4 and Letter) with a 100 mm black square and a caption naming the station, the id and the size. `markers/` is not in git. Reprint after changing `stations.dictionary`, `pick_id` or `drop_id`.

- Print at 100 % ("actual size", not "fit to page"), and check with a ruler that the black square measures what its caption says.
- Keep the white margin around the square when trimming: the detector needs that light border to find the black one.
- Mount the sheet flat and matte. A curl or a glare spot across the code costs more than a smaller print.

### Placing

Place each marker where the camera sees it within about 60° of face-on: on an upright or tilted card rather than flat on the table if the camera looks across the table. More slanted than about 65°, a marker is dropped by `stations.min_side_ratio`. Fast swings blur any marker; it is found again as the arm slows down. Keep the marker and its white margin a little inside the picture: on synthetic frames OpenCV missed a marker whose white margin ended a few pixels from the frame edge (see `NOT_BUILT.md`).

### Size and distance: assumptions, not measurements

The wrist camera's field of view has not been measured. The defaults assume 90° horizontal at 1280x720.

- A marker whose side is under 38 px (`stations.min_side: 0.03` of 1280) is ignored. On synthetic 1280x720 frames a marker was found in at least 9 frames out of 10 down to 12 px sharp and face-on, 34 to 36 px with 2 px of blur or 8 px of motion blur, and 44 px at 60° off face-on or with tilt, blur and dim light together. 16 px of motion blur needed 136 px.
- How far a 100 mm marker reaches, allowing 60 px at the far end, and how close it still fits whole in the frame:

  | Horizontal FOV | Found reliably out to | Whole in the frame down to |
  |---|---|---|
  | 60° | 18 x the side | 1.9 x the side (print 70 mm instead) |
  | 90° | 11 x the side (1.1 m) | 1.1 x the side (11 cm) |
  | 120° | 6 x the side (0.6 m) | 0.6 x the side |

  `uv run python scripts/make_markers.py --help` prints this table and the advice above.
- The station distance thresholds (`distance.thresholds` for `pick station` and `drop station`: `near` 0.03, `in_reach` 0.12 of the frame area) are computed for a 100 mm marker at 90°, not measured: `near` comes on at about 0.36 m (off again at 0.42 m) and `in_reach` at about 0.18 m (off at 0.21 m). `place` needs `near` (`phase.place_hint`), so under these assumptions it begins about 0.36 m from the marker. Check both with the real camera and a real print.
- A station's box, which the distance hint and `dx`, `dy` come from, is not the marker's outline: it is a square whose side is the mean of the marker's four sides, centred where the marker's diagonals cross (`aruco.py`). A marker turned in the picture therefore gives the same hint as an upright one: rendered through a 90° lens at four places in the frame, `near` and `in_reach` switched within 0.2 % of the upright marker's distance at any turn. The outline's bounds, used before, read a marker turned 45° as twice its area, so `near` came on at about 0.50 m instead of 0.36 m. In the overlay the square covers the middle of a turned marker and leaves its corners out. A square that would stick out of the frame is moved back inside whole, so the hint does not depend on where the marker is; its centre, and with it `dx`, `dy`, then moves by the overhang.
- A marker tilted away from the camera reads further than it is: at the frame centre by up to 6 % at 30° of tilt, 13 % at 45° and 22 % at 60°, and never more than 3 % closer. `near`, and with it `place`, comes on closer to the marker: on renders at 60° of tilt, at about 0.28 m instead of 0.36 m. In a phase walk on synthetic clips of the drop station marker, the marker turned 45° now reaches `near` at 19.93 s against 19.90 s upright (18.13 s before the change).

### False stations

A station that is not there would send the operator to the wrong place, so the filters favour no read over a wrong one. On 17,501 frames without markers (the 17 sample clips and `recordings/run1.mp4`), the shipped settings reported no station, and the second search of a mirrored picture (see "Camera"), run on the same frames flipped left to right, reported none either. Of all 50 ids, OpenCV read only 17 and 37 in those scenes (10 reads), never 9 or 15. If you change the ids, avoid 0 to 3, the ones most people print.

## Gripper pressure

Four thin-film pads sit on the gripper, in this order: left tip, left base, right tip, right base. A separate process publishes what they read on the `pressure` topic: `pressure_bridge.py` from the microcontroller over USB serial, or `mock_pressure.py` on a bench. Either one binds `transport.pressure_sub` (`tcp://127.0.0.1:5556`), so only one runs at a time. Vision connects at startup, whether or not anything publishes yet, and reads the feed on every pass of its loop, frames or not.

```
pressure {"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}
```

`t` is the time the sample was read, Unix seconds on this computer's clock. `raw` holds the four readings as sent. `norm` is 0 to 1 per pad after `pressure.calibration`: relative pressure, not a force. The mock publishes at `pressure.mock.rate_hz` (50 Hz, which the console expects).

### How contact is decided

The four pads become one grip level (`pressure.combine`):

- `both_fingers` (default): each finger counts its harder-pressed pad, and the level is the weaker of the two fingers. A held object presses on both fingers; one finger alone (brushing the table, pushing the object) is not a hold.
- `any_pad`: the hardest-pressed pad. Use it if one finger's pads are unreliable.
- `mean`: the average of the four. A fingertip pinch loads two pads, so it reads half as strong.

Contact turns on when the level stays at or above `on_threshold` (0.25) for `on_hold_s` (0.15 s), and off when it stays at or below `off_threshold` (0.12) for `off_hold_s` (0.30 s). In between, the state does not change. Contact is unknown when the newest reading is more than `stale_s` (0.25 s) old, or when two consecutive readings are more than `stale_s` apart (the feed was interrupted, or readings were lost in the queue): never holding, never open, never a drop. After unknown, a state needs its full hold time again. Staleness is judged on each reading's own `t`, so a reading that waited in a queue counts as old.

On every pass of its loop, vision takes every reading that arrived since the last pass and replays them in order, each on its own `t`. A slow pass therefore delays the decision but does not prevent it. A grip and a let-go that both fall within one pass are both replayed, but the phase machine sees only the state at the end of the pass.

### Real pads: the serial bridge

```powershell
uv run python -m paul_vision.pressure_bridge --port COM5          # --echo also prints every line read
```

**pyserial is not installed.** The bridge needs it to open the port, and it is not a dependency of this project; adding it is for the team to decide. Until then the bridge prints this and exits with status 3:

```
pressure_bridge needs pyserial, which is not installed. Fix: uv add pyserial (in computer_vision/, once the owner has approved the dependency)
```

Everything in the bridge except opening the port is plain Python and tested without pyserial. To find the port, look in Device Manager under "Ports (COM & LPT)", or run `[System.IO.Ports.SerialPort]::GetPortNames()` in PowerShell. The bridge's help suggests `python -m serial.tools.list_ports`, which also needs pyserial.

**The line format is an assumption** until the firmware exists: one line per sample, the four raw readings in pad order, separated by commas or spaces, ending in a newline, for example `1840,1322,1795,1290`. One trailing separator is accepted. Skipped and counted: blank lines, headers such as `lt,lb,rt,rb`, debug text, a wrong number of fields, anything that is not a finite number, lines over 256 bytes, and always the first line after the port opens (it may be the tail of a cut line). Each sample is stamped when its line is read, because the microcontroller's clock is not on Unix time. `pressure.bridge.baud` (115200) must match `Serial.begin()` in the firmware.

If the port disappears (cable pulled, board reset), the bridge tries again every `pressure.bridge.reopen_s` (1.0 s); vision keeps the phase for `phase.sensor_loss_hold_s` (2.0 s) meanwhile. Every `pressure.bridge.report_s` (10 s) it logs how many samples it published and which lines it skipped, and on exit it prints `published N messages; lines skipped: ...`. `--log FILE` writes every message published, in the format `mock_pressure --replay` reads. Exit status: 0 stopped normally, 1 the endpoint is taken, 2 bad arguments or config, 3 no pyserial.

### Calibration

`pressure.calibration` maps raw readings to `norm`, per pad: `(raw - raw_zero) / (raw_full - raw_zero)`, clipped to 0..1. `raw_zero` is the reading with nothing on the pad, `raw_full` the reading that counts as 1; `raw_full` may be below `raw_zero` if the circuit reads lower under pressure. The shipped values are the 12-bit ADC range, `[0, 0, 0, 0]` and `[4095, 4095, 4095, 4095]`, which is no calibration at all.

The bridge applies the calibration (vision reads `norm`), so restart the bridge after changing it. Not done yet; the plan: run the bridge with `--echo`, note each pad's reading with nothing on it and under a firm grip on a demo object, and enter them. Then record a few real grasps with `--log`, replay them with `mock_pressure --replay` while watching the overlay's `grip:` line, and set `on_threshold` and `off_threshold`.

## Gripper load

Until the pads publish, contact can come from the gripper servo's load instead (`paul_vision/gripper_load.py`). The arm controller (`../leslider_control/telemetry.py`) binds `transport.robot_sub` (`tcp://127.0.0.1:5558`) and publishes the `robot` topic at 10 Hz; the team launcher runs it with telemetry on (see "The team launcher"). Vision only subscribes. It never opens the servo bus.

```
robot {"t":1731542400.1,"pos":[50.1,12.0,80.3,40.0,0.0,35.2],"load":[0.01,0.12,-0.08,0.03,0.0,0.31],"control":{"input":"potentiometer","enabled":true,"input_fresh":true,"feedback":"measured","target":[50.0,12.0,80.0,40.0,0.0,35.0]}}
```

The values above are made up. Joints in the order shoulder pan, shoulder lift, elbow flex, wrist flex, wrist roll, gripper; the leslider controller adds a `slider` object, which vision ignores. `load` is each servo's present load / 1000, clipped to -1..1, or null when the bus read failed. `feedback` is `measured` for values read from the servos and `preview` in a dry run. `pos` for the gripper runs 0 to 100. `t` is stamped by the controller with `time.time()` just before the bus read, on this computer's clock (on Windows with Python before 3.13 it ticks every 15.6 ms, which is small against the 0.1 s period).

With `gripper_load.enabled: true`:

- The grip level is `sign * load` of the gripper, clipped to 0..1. With `empty_pos` set and the gripper position within `empty_margin` of it, the level is 0: jaws closed on nothing can still press on each other and show load.
- A message with `load: null` or `feedback: preview` gives no reading; after `stale_s` contact is unknown, as with silent pads (never holding, never open, never a drop). A message that is not a `robot` message as the controller builds it is ignored and counted, with a throttled warning.
- The same contact decision as the pads (hold times, staleness on each reading's own `t`, every reading since the last pass replayed in order), with the thresholds of the `gripper_load:` section.
- The pads are not read: one grip source at a time. The banner's grip line reads `grip:     gripper load from tcp://127.0.0.1:5558` instead of `pressure: ...`, and the overlay's `grip (load): holding 0.31` or `grip (load): no load data`.

At 10 Hz the load is slower than the pads at 50 Hz: contact needs at least `on_hold_s` (0.2 s) plus up to one period to turn on, and a let-go in `carry` takes `off_hold_s` (0.3 s) plus up to one period to give `dropped`.

| Key | Shipped | Meaning |
|---|---|---|
| `enabled` | `false` | `true`: grip decisions come from the gripper load, and `pressure` is not read |
| `sign` | `1` | `1` or `-1`: the load sign that means the jaws press closed |
| `on_threshold` | `0.25` | level at or above this for `on_hold_s` turns contact on |
| `off_threshold` | `0.12` | level at or below this for `off_hold_s` turns it off; below `on_threshold` |
| `on_hold_s` | `0.2` | 3 samples at 10 Hz |
| `off_hold_s` | `0.3` | |
| `stale_s` | `0.35` | no reading newer than this, or two this far apart: unknown. Must be above 0.1 s, the period |
| `empty_pos` | `null` | the gripper position with the jaws closed on nothing; `null`: no check |
| `empty_margin` | `3.0` | how close to `empty_pos` counts as closed on nothing |

**It ships off, and the thresholds are placeholders.** No grasp has been recorded: the sign of the gripper's load when it holds something, the levels when holding and when closed on nothing, and `empty_pos` are not known. Everything here has run only in tests, with fake `robot` messages (`tests/test_gripper_load.py`, and grasp, carry and drop through `run()` in `tests/test_pipeline_e2e.py`).

To calibrate, with a person at the arm:

1. In one terminal at the repo root, the full launcher: `.\demo.ps1` (the controller takes the keyboard; E enables it).
2. In another, in this folder: `uv run python -m paul_vision.subscribe_print --topic robot --raw | Out-File -Encoding utf8 recordings\robot-grasps-<date>.log` (in Windows PowerShell 5.1, `>` writes UTF-16).
3. Grasp each demo object about five times, close the gripper on nothing five times, and move the arm freely while holding an object five times. Note the times.
4. From the log: the sign of the gripper's load when it holds, the levels when holding and when not, and the gripper position when closed on nothing. Set `sign`, `on_threshold`, `off_threshold` and `empty_pos`, then `enabled: true`.
5. Run once more: approach, grasp, carry, and a let-go in `carry`, which should give `alert: "dropped"` and "Grip lost. Object may have dropped.".

## Configuration

Every threshold lives in `config.yaml`, with a comment on each key. Unknown or missing keys stop startup. The ones you will touch first:

- `camera.wrist_index`, `camera.rotate_deg`, `camera.flip_h`, `camera.flip_v`
- `detector.classes` (then re-bake)
- `selection.conf_lock` / `conf_keep`: confidence to acquire and to keep a target
- `selection.coast_s`: how long guidance is held when the target misses frames
- `tracker.label_memory_s` / `label_margin`: how readily a tracked object changes its name
- `guidance.deadzone_enter` / `deadzone_exit`: when `aligned` turns on and off
- `distance.thresholds`: box area fractions for `near` and `in_reach`, per label (the station entries decide when `place` begins). Startup checks that each entry has `0 < near < in_reach` and `in_reach * (1 + distance.hysteresis) < 1` (with the hysteresis at 0.15, `in_reach` below about 0.87): a target already seen must pass `in_reach` by the hysteresis margin, and a box never fills more than the whole frame.
- `summary.min_interval_s`: minimum gap between spoken sentences

Milestone 2 added four sections and changed one key. Tune them in this order once the hardware is on the bench: `pressure.calibration`, then `pressure.on_threshold` / `off_threshold`, then the `drop station` entry in `distance.thresholds` (with the real camera and print), then `phase.place_hint` and the dwell times.

`pressure:` (none of these has been checked on the hardware)

| Key | Default | What it does |
|---|---|---|
| `combine` | `both_fingers` | How the four pads become one grip level: `both_fingers`, `any_pad` or `mean`. |
| `on_threshold`, `on_hold_s` | 0.25, 0.15 s | Contact turns on at or above this level, held this long. A shorter squeeze is a bump. |
| `off_threshold`, `off_hold_s` | 0.12, 0.30 s | Contact turns off at or below this level, held this long. Must be below `on_threshold`. |
| `stale_s` | 0.25 s | Contact is unknown when the newest reading is older than this, or when two consecutive readings are further apart than this. 12 samples at 50 Hz; USB serial on Windows arrives in bursts, so much shorter values would flicker. |
| `calibration.raw_zero`, `raw_full` | `[0, 0, 0, 0]`, `[4095, 4095, 4095, 4095]` | Raw reading for 0 and for 1, per pad. |
| `mock.rate_hz`, `ramp_s`, `contact_level` | 50, 0.2 s, 0.6 | The mock's rate, ramp time and toggle level. The rate must be above `1 / stale_s` (the mock warns). |
| `bridge.baud`, `read_timeout_s`, `reopen_s`, `report_s` | 115200, 0.05 s, 1.0 s, 10.0 s | Serial speed, longest single read, wait before reopening a lost port, period of the status line. |

`phase:`

| Key | Default | What it does |
|---|---|---|
| `grasp_settle_s` | 0.5 s | Contact must hold this long before `grasp` becomes `carry`. |
| `release_hold_s` | 1.0 s | `release` lasts this long, time to withdraw from the placed object. |
| `place_hint`, `place_dwell_s` | `near`, 0.5 s | `carry` becomes `place` when the drop station is at this hint or closer for this long. `near`, not `in_reach`: in the last centimetres the held object covers the marker. |
| `leave_place_hint`, `leave_place_dwell_s` | `far`, 1.0 s | `place` goes back to `carry` when the station is at this hint or further for this long. Must be further than `place_hint`. |
| `alert_hold_s` | 1.0 s | How long `alert: "dropped"` is repeated. |
| `sensor_loss_hold_s` | 2.0 s | How long a pad-started phase survives without pad data. |
| `blind_spot_hint`, `blind_spot_s` | `in_reach`, 1.5 s | The grasp blind spot (see "Phases"). `0` turns it off. |

`stations:`

| Key | Default | What it does |
|---|---|---|
| `enabled` | true | Look for the station markers at all. |
| `dictionary` | `DICT_4X4_50` | The ArUco dictionary; 4x4 cells read from furthest away. |
| `pick_id`, `drop_id` | 9, 15 | The two marker ids; every other id is ignored. |
| `min_side` | 0.03 | Markers whose mean side is shorter than this fraction of the frame's longer side are ignored (38 px at 1280x720). |
| `min_side_ratio` | 0.40 | Shortest side over longest side as seen. Drops slivers, and markers seen more than about 65° off face-on. |
| `detect_max_px` | 1280 | Markers are searched in a grayscale copy whose longer side is at most this (0 = full size). 960 saves about 1 ms but needs markers 1.5 times bigger at a slant. |
| `min_confidence` | 0.75 | Floor on OpenCV's rating of each read; drops broken reads. Keep it at or above `selection.conf_lock`. |
| `error_correction_rate` | 0.0 | Share of the dictionary's correctable bits that may be wrong. 0 means exact matches only. |

`distance.thresholds` gained `pick station` and `drop station` entries (see "Station markers"). `transport.command_sub` is now a list of endpoints, one per command sender (see "Commands").

Milestones 4 and 5 added two sections, and the gripper load a third (`gripper_load:`, see "Gripper load" for its table).

`labels:` (see "Teaching vision a name")

| Key | Default | What it does |
|---|---|---|
| `dir` | `labels` | Where the label library lives, relative to `config.yaml`. Not in git: it holds camera pictures. The team launcher's copy of the config points it at this folder's `labels` (see "The team launcher"). |
| `max_tags`, `max_views` | 20, 5 | Most names in the library, and most views per name. Every view is one more class in the detector, which slows down a little with each. |
| `max_name_chars`, `max_prompt_chars` | 40, 100 | Longest name (after lower case and single spaces) and longest `tag_text` description. Voice uses the same name limit. |
| `snap_iou` | 0.5 | A box given with `tag_visual` that overlaps a tracked object at least this much is replaced by that object's box. |
| `border_margin` | 0.01 | A box closer than this to an edge of the picture (fraction of the frame) is refused. |
| `min_box_side` | 0.04 | Each side of the box must be at least this fraction of the frame's side. |
| `worker_timeout_s` | 120 s | The label worker is stopped past this. On the laptop it took about 7 s on PyTorch and 13 to 14 s with an OpenVINO export. |
| `worker_threads` | 2 | PyTorch threads the label worker may use; the frame loop keeps the rest of the CPU. |
| `load_timeout_s` | 90 s | OpenVINO only: loading and compiling a new export, which took 2.3 to 31 s on the Intel GPU. Past this the change is reported as failed. |

`voice:` (see "Voice commands"; nothing here has been tried with a microphone)

| Key | Default | What it does |
|---|---|---|
| `endpoint` | `tcp://127.0.0.1:5559` | Where the voice process binds. Must be one of `transport.command_sub`, or vision never hears it; startup refuses anything else. |
| `wake_words` | `[paul, hey paul, ok paul, okay paul]` | An utterance counts only if it starts with one; `[]` = every utterance counts. Lower case, single spaces. Add a spelling if a recognizer writes the name differently. |
| `label_cutoff`, `label_margin` | 0.9, 0.1 | A spoken label or phase that is not exact must be this similar to a known one (0 to 1), and lead the next best by this much, or nothing is sent; when as many words are said as the label has, each word must also be this similar to the word said in its place (see "It never guesses" under "Voice commands"). "pillbottle" scores 0.95 against pill bottle and is taken; "bottel" scores 0.83 against bottle, "cop" 0.67 against cup, and both are refused. |
| `max_utterance_chars` | 200 | A longer line is not a command. |
| `deliver_wait_s` | 2.0 s | How long a command waits for vision to subscribe before it is reported as not delivered and dropped. |
| `listen.culture` | `en-US` | Which installed Windows recognizer `--listen` uses. |
| `listen.min_confidence` | 0.7 | Recognitions below this are printed and never sent. |
| `listen.labels_refresh_s` | 2.0 s | How often `--listen` checks the tags; a change rebuilds its grammar. |
| `listen.decoy` | true | Free dictation beside the commands, never sent, so speech that is not a command lands there instead of on the nearest command. |
| `listen.adaptation` | false | Let the recognizer adapt to the voice it hears. On, a second voice after the first missed 4 to 16 of 39 commands per speaking rate. |
| `listen.dictated_names` | false | Take new tag names ("this is ...", "learn ...") from dictation. Off, because dictation got 2 of 12 names wrong at confidence 0.95 and above; names are typed instead. |
| `listen.decoy_words` | `[bowl, paula]` | Sound-alikes of the wake words and labels, in the grammar beside them and never sent, so that the recognizer can hear them as themselves. Lower-case words of letters, no duplicates, no wake word; one said like a known label is left out. They took the wrong sends in the loopback test from 13 to 7 of 102. "cap" is not in the list because it turned 6 of 54 "cup" commands into "cap"; a sound-alike that is not listed ("cop", "cub", "books") is still heard as the label. |

## Camera

The live console's **Camera source** selector lists device names using the configured
OpenCV backend. **Refresh** rescans without opening cameras. Selecting a camera
prepares it in the background, keeps the current feed until a new frame arrives,
then resets tracking and guidance for the new scene. Failure keeps the old source.
The runtime selection lasts until restart; `camera.wrist_index` remains the startup
default. Switching is disabled with `--record` to preserve the video's dimensions.
The MJPEG server exposes `GET /sources?refresh=1` and `POST /source` (`{"id":"…"}`);
browser access to these controls is restricted to local console origins.

- **Orientation.** Run with the overlay and move an object to the gripper's right: it must move right in the window, and the arrow must point right. If not, set `rotate_deg` / `flip_h` / `flip_v`. The image itself is turned before detection, so the overlay, `bbox` and `dx`/`dy` always agree. For a 90° or 270° mount also swap `detector.imgsz` to `[640, 384]`.
- **Mirrored picture and the station markers.** With exactly one of `flip_h` and `flip_v` set, the picture is mirrored (`rotate_deg` never mirrors, and both flips together are a half turn). A mirrored marker is another code: the drop marker (id 15) does not read at all, the pick marker (id 9) reads as itself. So vision then searches each frame for the markers a second time, flipped back left to right, and keeps for each station the better of the two reads; the banner's `stations:` line says so. That costs about twice the marker-search time: 4.9 to 5.0 ms against 2.4 to 3.2 ms on one synthetic 1280x720 frame, not measured in the pipeline. Mirrored markers have been read only on synthetic pictures, not through a real mirrored camera (`NOT_BUILT.md`).
- **`--source wrist` is only an index.** It opens the camera at `camera.wrist_index` (0), whatever camera that is: on a laptop without the wrist camera, the built-in one. Nothing warns, and the log says only `source: camera 0`. The team launcher passes `wrist` unless told otherwise (`-CameraSource`).
- **Finding the index.** `uv run python scripts/list_cameras.py` lists every camera with its real frame rate and saves a picture of each to `snapshots/`.
- **30 FPS needs MJPG.** Over USB 2.0 the camera only delivers 1280x720 at 30 FPS as MJPG (`camera.fourcc`). With DirectShow the format has to be requested after the resolution, or the driver silently falls back to YUY2 at 10 FPS; `sources.py` does this and logs the format it got.
- **Light.** The detector needs a lit scene. In a dim room the wrist view was nearly black and nothing was detected.
- If the camera stops delivering frames it is reopened automatically after `camera.reopen_after_s`; meanwhile subscribers see `degraded`. A camera that was just opened gets `camera.first_frame_s` for its first frame instead: the laptop's built-in camera takes 1.1 s, and with the shorter limit it was reopened over and over and never started.
- **A built-in camera slows down by itself.** The laptop's delivered 30, 18 and 10 FPS on different runs while reporting 30. The log line shows it as a long `wait for frame`. The cause was not established; exposure time in a dim room is the usual one.

## Testing on recorded clips

```powershell
uv run python scripts/eval_clips.py sample_videos                      # every clip in the folder
uv run python scripts/eval_clips.py sample_videos --sheets snapshots   # also save a sheet of annotated stills per clip
uv run python scripts/eval_clips.py recordings/run1.mp4                # one clip, for example one made with --record
```

`sample_videos/` holds 17 wrist-camera clips (8.7 minutes) from public SO-100 and SO-101 datasets: cups, bottles, pill bottles and boxes, in good and bad light. The folder is not in git; `sample_videos/README.md` lists where each clip came from. None of it is our camera or our objects, so read the results as a list of what goes wrong, not as a score for the demo.

The script runs the real object detector and pipeline on every frame and takes time from the clip, so the report depends only on the clips and the config, not on how fast the machine is. It runs neither the station markers nor the pads, so it only ever sees `search` and `approach`. All 17 clips take about 7 minutes on the Intel laptop. For each clip it prints:

| Line | Meaning |
|---|---|
| `status` | Share of frames with each status. |
| `target` | The names the locked target had, as shares of the locked frames. |
| `locks` | How many times a target was acquired, how long the locks lasted, and how often the target was renamed while locked. |
| `strongest` | How often each name belonged to the most confident object in view, and its median confidence. |
| `said` | Every sentence the operator would have heard, with its time. |

### What the sample clips showed

Default config, `yoloe-26s` through OpenVINO. Over all clips: `ok` in 26% of frames, `low_confidence` in 9%, `no_target` in 64%; 98 locks, 89 sentences.

| Situation | Clips | Result |
|---|---|---|
| Upright paper cup, seen from the front | `wrist_so101_paper_cup` | Works. Locked for 72% of the clip in two locks (8 s and 21 s), always "cup", median confidence 0.95, sensible sentences. |
| Nothing of ours in view | `wrist_so100_wood_block_at_home_1080p` | Correctly quiet: `no_target` 98%, never locked. |
| Pill bottle on a table | `wrist_so100_pill_bottle_1`, `_2` | One steady lock per clip (8 s and 12 s). The name is the problem: "bottle" from the side, often "cup" when seen cap-first. One clip stays "bottle" throughout, the other turns into "cup" halfway. Never "pill bottle". |
| Medicine bottle seen from above | `wrist_so101_medicine_bottle_portrait` | Locked for 40% of the clip, and called "cup" in 89% of the locked frames. |
| Juice bottle, dark scene | `wrist_so101_bottle_1`, `_2` | Locked about half the time, in short locks. Called "bottle" in 79% of the locked frames of one clip and 44% of the other; "cup" the rest of the time. |
| Several paper cups, very close | `wrist_so101_stack_cups` | Right name (99%), but 20 locks in 40 s: a cup that fills the frame is no longer detected. |
| Cup lying on its side, tilted camera | `wrist_so100_cup_1`, `_2` | Not detected (`no_target` 93 to 94%). |
| Small blocks and trays | `wrist_so101_blue_box_720p`, `wrist_so101_box` | Nothing to lock on (`no_target` 93 to 98%). "box" does not match them. |
| Dark or blurred scenes, clutter | `wrist_so101_medkit_bottle`, `wrist_so101_wrapped_can_dark`, `wrist_so101_cup_lighting`, `wrist_so101_cup_distractors`, `wrist_so101_cup` | Confidence around 0.2 to 0.35. Locks are rare or last under half a second. |

What follows from it:

- **Names are unreliable between cup, bottle and pill bottle.** The same object gets a different name from a different side. The tracker settles one name per object by vote (`tracker.label_memory_s`, `label_margin`), which keeps the name steady: in the clips with one kind of object, the name the operator hears changed 3 times, against 9 with the earlier rule (a new name had to last 0.3 s). It does not make the name right: in those clips the locked target still has the wrong name in 26% of its frames (29% before). Before the demo, record our own objects with `--record`, run this script on the recordings, and adjust `detector.classes` until the names hold.
- **Vision goes blind at grasp range.** An object that fills the frame is no longer detected, so the target disappears just before the grasp. The phases rely on the pads there: the blind spot keeps `approach` and holds back "Target lost." while the gripper closes (see "Phases"), but only while pad readings arrive.
- **Light and a clear view matter more than any threshold.** Every clip that works is well lit with the object upright. Dark, blurred or tilted views give low confidence that no setting rescues.
- **Half of all locks last half a second or less**, mostly because the detector loses the object for more than `selection.coast_s`. Replaying the same detections with other settings: `coast_s: 0.3` gives 75 locks with a median of 0.9 s, but guidance then points at the last seen position twice as long after the target is really gone; `conf_keep: 0.15` gives 72 locks with a median of 0.8 s and more wrong names (29%). Neither default was changed. Decide on footage from our own camera.

## Performance on the Intel laptop

i9-13900H with Iris Xe, wrist camera at 1280x720, `yoloe-26s` at 384x640 through OpenVINO, before the station markers existed:

| Run | FPS | Detect | Capture → publish |
|---|---|---|---|
| Headless, machine otherwise idle | 29.5 (camera limit is 30) | 13.5 ms (p95 16.2) | 13.7 ms (p95 16.4) |
| Overlay window + `--record`, idle | 29.5 | 14.9 ms (p95 18.0) | 15.2 ms (p95 18.4) |
| Overlay window + `--record`, Chrome using about 28% of the GPU | 20.5 | 34.6 ms (p95 45.6) | 53.3 ms (p95 74.6) |
| Headless, Chrome using 25 to 50% of the GPU | 22.3 | 42.7 ms (p95 71.5) | 58.8 ms (p95 96.5) |

The integrated GPU is shared with everything on screen, and this laptop also clocks its CPU down under sustained load. A browser playing video or a game in the background can halve the frame rate. For a test that matters, close them and stay on mains power. Once, a phase walk on the bench, run while other heavy jobs loaded the laptop, dropped to 2 to 8 FPS for about 16 s and went `degraded` three times (longest gap 659 ms), though contact was still decided correctly; the cause of the drop was not confirmed.

**Station markers cost about 8 ms a frame.** The log line shows each detector separately (`detect yolo`, `detect aruco`). Same laptop, headless, a 1280x720 clip paced at 30 FPS, 450 frames, three runs each:

| Stations | FPS | Detect | Latency |
|---|---|---|---|
| on (default) | 28.5 (28.2 to 28.7) | 29.8 ms (`yolo` about 21.4 + `aruco` 8.0 to 8.2) | 30 ms (p95 about 33) |
| off (`stations.enabled: false`) | 30.0 | 21.6 ms | 22 ms |

Measured on its own, outside the pipeline, the marker search took 4 to 5 ms a frame (p95 7.5 ms) at full size.

If it is still too slow: set `detector.weights.openvino` to `yoloe-26n-seg.pt` (faster, but it misses small and distant objects), or lower `overlay.max_width`. `stations.detect_max_px: 960` saves about 1 ms on the marker search; it has not been tried in the pipeline.

The message is published before the overlay is drawn, and recording runs on its own thread, so neither adds to guidance latency.

## Layout

```
config.yaml            every threshold and endpoint
paul_vision/
  __main__.py          command line
  app.py               Pipeline (one frame and the pads' state in, one message out), main loop, Recorder
  sources.py           camera (background thread, newest frame, reopen), video file, orientation
  camera_control.py    camera list and switching for the console
  detector.py          Detector interface, profile selection, Ultralytics YOLOE, CompositeDetector, class-list swap for tags
  aruco.py             station markers (pick and drop) as a second detector; the square station box
  bake.py              class list -> prompt embeddings and OpenVINO export
  labels.py            the label library on disk: names, views, embeddings; the offline `labels` commands
  label_manager.py     vision's side of tag_visual, tag_text and untag: checks, answers, the worker, the swap
  label_worker.py      child process that makes a view's embedding and the OpenVINO export
  tracker.py           IoU tracker: stable track IDs, one settled label per object, stations by their id
  selection.py         which track is the target: objects or the drop station, lock/keep, switch hysteresis, coasting
  guidance.py          dx/dy smoothing, deadzone, distance hint
  summary.py           spoken sentences: templates, rate limit, settle time, phase events
  phase.py             the six phases and the dropped alert
  pressure.py          the pressure topic, the contact decision, the receiver and publisher
  gripper_load.py      contact from the gripper servo's load (the arm controller's robot topic)
  mock_pressure.py     stand-in for the pads: keys, a script or a replayed log
  pressure_bridge.py   serial lines from the pad microcontroller -> pressure (needs pyserial)
  messages.py          the vision and pressure messages: build, encode, decode, validate; the robot topic name
  transport.py         publisher thread with the stall watchdog; Subscriber
  commands.py          the command topic: select_target, clear_target, set_phase, tag_visual, tag_text, untag
  send_command.py      sends one command from a terminal
  voice.py             the voice process: utterances as text -> commands; the --listen stand-in
  stream.py            the camera picture for the web console, MJPEG over HTTP
  overlay.py           debug window
  metrics.py           FPS and per-stage timing
  subscribe_print.py   prints any topic; reference subscriber
scripts/
  list_cameras.py      every camera with its real frame rate and a snapshot
  eval_clips.py        report on recorded clips: status, locks, renames, spoken sentences
  make_markers.py      print-ready PNGs of the two station markers
  voice_listen.ps1     the --listen stand-in recognizer (Windows System.Speech, Windows PowerShell 5.1)
  voice_loopback.py    measures the stand-in with synthetic speech, no microphone
  try_visual_prompts.py  the experiment behind tag_visual: one object's picture as a class, on a clip
tests/                 pure logic plus end-to-end tests over real ZMQ; no camera, GPU or pads needed
sample_videos/         wrist-camera test clips (gitignored; README.md inside lists the sources)
models/                weights and baked artifacts (gitignored)
markers/               the marker PNGs from make_markers.py (gitignored)
labels/                the label library: taught names, their pictures and embeddings (gitignored)
```

## Troubleshooting

- **`could not bind vision publisher`**: another copy is still running and holds port 5555.
- **`the loaded model detects [...], not the configured [...]`**: run `uv run python -m paul_vision.bake --force`.
- **Wrong camera or no frames**: run `scripts/list_cameras.py` and fix `camera.wrist_index`. Through the team launcher, `-CameraSource wrist` (the default) opens that index; pass another index or a video file to override it.
- **Slow**: read the banner (is it the profile you expect?), then the per-stage numbers in the log line. A high `detect` on the laptop usually means something else is using the GPU; a high `detect aruco` is the marker search (see "Performance").
- **Subscriber prints nothing**: it reports after 2 s of silence. Check that the publisher is running and that both use the same `transport.vision_pub`.
- **The phase never gets past `approach`**: no pad data. The overlay says `grip: no pressure data`. Start the mock or the bridge, and check that it uses the same `transport.pressure_sub`. With `gripper_load.enabled` it says `grip (load): no load data`: the arm controller is not running, runs as a dry run (`feedback: preview`), cannot read the servos (`load: null`), or publishes somewhere other than `transport.robot_sub` (the launcher's `--robot-pub-port`). `subscribe_print --topic robot` shows which.
- **The mock or the bridge cannot bind**: another mock or bridge is running on `transport.pressure_sub`.
- **`carry` never becomes `place`**: is the drop station found at all (magenta or locked box in the overlay)? Check the banner's `stations:` line, the print size, the slant, and the light. Then check `distance.thresholds` for `drop station`: `place` needs `near`.
- **"Grip sensor lost." at the end of a mock script**: the script ended within `off_hold_s` of its last change. End it with a hold step.
- **send_command: `is in use by another process`**: another sender holds that port. Pass `--endpoint` with another entry of `transport.command_sub`.
- **send_command: `nobody subscribed`**: vision is not running, or its config does not list that endpoint in `transport.command_sub`.
- **pressure_bridge exits with status 3**: pyserial is not installed (see "Real pads: the serial bridge").
- **`configuration: ...`** (status 2): the config cannot be loaded. Vision writes it as an `ERROR` log line; voice and send_command write one line on stderr starting `voice: configuration:` or `send_command: configuration:`. The file cannot be opened, is not UTF-8 text, or is not valid YAML (the message names the file, the line and the column), or a key is unknown, missing or out of range (the message names the key, for example `voice.label_cutoff must be in (0, 1]`). Vision reports a `--target-label` that is neither a class nor a tag the same way.
- **`configuration: the label library cannot be read: ...`** (vision exits with status 2): `labels/library.json` cannot be opened, is not valid JSON, or has another version or shape than this code reads; the message says which. Repair or delete it, or point `labels.dir` at another folder.
- **`startup failed: the label library in ... could not be made ready: ...`** (status 1): rebuilding the library or its OpenVINO export failed. The message lists the three ways out: `labels rebuild`, `labels remove <name>`, or moving the folder away to start without tags (see "The library on disk").
- **A label command did nothing**: vision answers every `tag_visual`, `tag_text` and `untag` in `summary`, and logs it. An answer whose next frame came more than `watchdog.stall_timeout_s` (0.5 s) after it (the feed stalled or was paused) is not sent; the log has `announcement dropped, ... s old when the next frame came: ...` instead. Watch `subscribe_print`, or read the log; the reasons are in the table under "Teaching vision a name". "Cannot learn ...: nothing is locked." or "... out of view." usually means trying again a moment later, with the object locked and in view.
- **voice: `(voice.endpoint) is in use by another process`**: another voice process is running.
- **voice: `NOT DELIVERED`**: vision is not running yet, or runs with a config whose `transport.command_sub` does not list `voice.endpoint`. Say it again once vision runs; nothing was queued.
- **voice: every line is `ignored: no wake word`**: start each utterance with one of `voice.wake_words`, or, when typing, start it with `--no-wake-word`.
- **voice: `warning: the label library cannot be read`**: voice matches `detector.classes` only until `labels/library.json` reads again; tags cannot be selected or forgotten by voice meanwhile.
- **voice: `warning: ...\library.json has tag names voice cannot use, left out: ...`**: the library holds tag names that are not text, have no words (only punctuation), or break the name rules under "Teaching vision a name", for example after the file was edited by hand. Voice leaves those names out, so they cannot be selected or forgotten by voice; the other tags work. The warning is printed again only when those names change.
- **voice: `cannot start the stand-in recognizer`**: `--listen` needs Windows PowerShell 5.1 (`powershell.exe`) and `scripts/voice_listen.ps1`. Without `--listen`, typed or piped lines still work.
- **voice: `the stand-in recognizer stopped (exit N): ...`**: what follows the colon is the recognizer's own error output. Voice exits with status 1; start it again, or go on without `--listen`.
