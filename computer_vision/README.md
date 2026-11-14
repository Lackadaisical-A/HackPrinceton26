# PAUL vision

Finds objects in the wrist-camera feed, picks a target, and publishes guidance ("move so the target is centred") over ZMQ. Other processes turn that into haptic pulses and speech. Vision assists the operator and never controls the arm.

Milestone 1 is built: frame sources, YOLOE detection, tracking, target selection, guidance, spoken summaries, the `vision` publisher with a stall watchdog, the debug overlay, and tests. Built since: the `command` receiver and the camera picture for the web console (see "Web console" below). What is not built or not verified yet (stations, pressure, the later phases, user labelling, speech to text, the CUDA run) is listed in `NOT_BUILT.md`.

## Run it

```powershell
uv sync                                         # once
uv run python -m paul_vision.bake               # once, and after changing detector.classes, imgsz or weights
uv run python -m paul_vision --source wrist     # overlay window + publisher
uv run python -m paul_vision.subscribe_print    # second terminal: prints what subscribers receive
uv run pytest
uv run python scripts/eval_clips.py sample_videos   # report on recorded clips (see "Testing on recorded clips")
```

`--source` takes `wrist` (the camera at `camera.wrist_index`), a camera index such as `0`, or a video file.

| Flag | Effect |
|---|---|
| `--no-overlay` | Headless. |
| `--max-frames N` | Stop after N frames and print the per-stage timing table. |
| `--record FILE` | Also save the frames to `FILE` (`.avi` or `.mp4`); replay it later with `--source FILE`. |
| `--video-end exit\|loop\|hold` | What a video file does at its end. `hold` stalls the feed so subscribers see `degraded`. |
| `--target-label LABEL` | Only target this label. |
| `--backend`, `--device` | Override the detector profile, e.g. `--backend torch --device cpu`. |
| `--config FILE` | Use another config file. |

Overlay keys: `q` or `Esc` quits, `s` saves a snapshot to `snapshots/`, `p` pauses (publishing stops, so subscribers see `degraded`).

The first start takes 10 to 20 s on the Intel laptop while OpenVINO compiles the network for the GPU.

## Two detector profiles

The profile is chosen at startup and printed in a banner; check that it says what you expect.

| Machine | Profile | How |
|---|---|---|
| NVIDIA GPU (demo machine) | `torch on CUDA` | PyTorch, half precision |
| Intel laptop | `openvino on Intel GPU` | OpenVINO export on the integrated GPU, CPU if there is none |

Both use `yoloe-26s`, so thresholds tuned on one machine carry over to the other.

`python -m paul_vision.bake` turns the class list in `config.yaml` into prompt embeddings (and an OpenVINO export for the Intel profile) under `models/`. The first bake downloads the weights (30 MB) and a text encoder (243 MB), so do it on good wifi. After that, startup needs no network. `models/` is gitignored: bake on each machine, or copy the folder across.

### Demo machine bring-up

The CUDA profile has not been run yet: this laptop has no NVIDIA GPU. First run on the demo machine:

1. `uv sync --extra cu130`
2. `uv run --extra cu130 python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` must print `True`. If it prints `False`, update the NVIDIA driver (the wheels are built for CUDA 13.0).
3. `uv run --extra cu130 python -m paul_vision.bake` (or copy `models/` from the laptop first to skip the downloads).
4. `uv run --extra cu130 python scripts/list_cameras.py`, then set `camera.wrist_index` in `config.yaml`.
5. `uv run --extra cu130 python -m paul_vision --source wrist --no-overlay --max-frames 300`. The banner must say `torch on CUDA`; note the FPS and latency.
6. `uv run --extra cu130 python -m paul_vision --source wrist` and check the orientation (below).

Always pass `--extra cu130` on that machine. A plain `uv run` re-syncs the environment to the CPU build of PyTorch.

## For subscribers

The publisher binds `tcp://127.0.0.1:5555`; subscribers connect. Each message is one ZMQ frame: the topic, a space, then compact JSON.

```
vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{"track_id":7,"label":"cup","source":"yolo","bbox":[0.41,0.3,0.62,0.58],"confidence":0.91},"guidance":{"dx":-0.12,"dy":0.05,"distance_hint":"near","aligned":false},"summary":"Cup near, slightly left."}
```

| Field | Meaning |
|---|---|
| `t` | Capture time of the frame, Unix seconds. |
| `frame_id` | Frame number from the source. Gaps mean skipped frames. |
| `status` | `ok`: target locked. `low_confidence`: something is visible but not sure enough to lock. `no_target`: nothing visible. `degraded`: no fresh frames. |
| `phase` | `search` or `approach` (more in Milestone 2). |
| `target` | `null` unless `status` is `ok`. `bbox` is `[x1, y1, x2, y2]`, each 0 to 1. |
| `guidance` | `null` unless `status` is `ok`. `dx`, `dy` are −1 to 1, target centre minus frame centre; positive is right and down in gripper terms. `distance_hint` is `far`, `near` or `in_reach`. `aligned` is true inside the deadzone. |
| `summary` | A sentence to speak, or `null` when there is nothing new to say. |

Two rules for every consumer:

- **Act only on `status == "ok"`.** Everything else means no guidance.
- **Treat silence, or a `t` older than 0.5 s, as no guidance.** The watchdog publishes `degraded` every 0.5 s while the feed is stalled, but it cannot report its own process dying.

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
    if msg["status"] != "ok" or time.time() - msg["t"] > 0.5:
        stop_guidance(); continue
    pulse(msg["guidance"]["dx"], msg["guidance"]["dy"], msg["guidance"]["aligned"])
```

`paul_vision/subscribe_print.py` is the reference implementation, and `paul_vision.messages.validate_vision` checks a message against the contract.

## Web console

The console in `../frontend` shows the camera picture with the guidance drawn over it. It needs vision and the bridge (`../bridge`), each in its own terminal, started in any order:

```powershell
uv run python -m paul_vision --source wrist     # here; --source 0 for a laptop's built-in camera
uv run python -m paul_bridge                    # in ..ridge
npm run dev                                     # in ..rontend; then Signals, "Live bridge", Connect
```

- **Picture.** Vision serves the frames it is processing as MJPEG at `http://127.0.0.1:8766/stream` (`stream:` in `config.yaml`; `--no-stream` turns it off). The console cannot open the camera itself: on Windows a camera held by one program is not available to another. Nothing is encoded while no one is watching.
- **Messages.** The bridge relays the `vision` topic to the console over a WebSocket.
- **Commands.** The console's target buttons send `select_target` and `clear_target` through the bridge, which binds `transport.command_sub` (port 5557). `select_target` limits the target to one of `detector.classes`; anything else is logged and ignored. `set_phase` is received and ignored.

Run on 14 November 2026 on the laptop with its built-in camera (`--source 0`), the bridge, and the console in headless Chrome: the picture and the overlay showed, both commands took effect, and the console recovered by itself when vision was stopped and started again. The wrist camera was not plugged in for that run.

## Configuration

Every threshold lives in `config.yaml`, with a comment on each key. Unknown or missing keys stop startup. The ones you will touch first:

- `camera.wrist_index`, `camera.rotate_deg`, `camera.flip_h`, `camera.flip_v`
- `detector.classes` (then re-bake)
- `selection.conf_lock` / `conf_keep`: confidence to acquire and to keep a target
- `selection.coast_s`: how long guidance is held when the target misses frames
- `tracker.label_memory_s` / `label_margin`: how readily a tracked object changes its name
- `guidance.deadzone_enter` / `deadzone_exit`: when `aligned` turns on and off
- `distance.thresholds`: box area fractions for `near` and `in_reach`, per label
- `summary.min_interval_s`: minimum gap between spoken sentences

## Camera

- **Orientation.** Run with the overlay and move an object to the gripper's right: it must move right in the window, and the arrow must point right. If not, set `rotate_deg` / `flip_h` / `flip_v`. The image itself is turned before detection, so the overlay, `bbox` and `dx`/`dy` always agree. For a 90° or 270° mount also swap `detector.imgsz` to `[640, 384]`.
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

The script runs the real detector and pipeline on every frame and takes time from the clip, so the report depends only on the clips and the config, not on how fast the machine is. All 17 clips take about 7 minutes on the Intel laptop. For each clip it prints:

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
- **Vision goes blind at grasp range.** An object that fills the frame is no longer detected, so "Target lost." comes just before the grasp. The grasp phase in Milestone 2 has to rely on the pressure sensors there, not on the target staying visible.
- **Light and a clear view matter more than any threshold.** Every clip that works is well lit with the object upright. Dark, blurred or tilted views give low confidence that no setting rescues.
- **Half of all locks last half a second or less**, mostly because the detector loses the object for more than `selection.coast_s`. Replaying the same detections with other settings: `coast_s: 0.3` gives 75 locks with a median of 0.9 s, but guidance then points at the last seen position twice as long after the target is really gone; `conf_keep: 0.15` gives 72 locks with a median of 0.8 s and more wrong names (29%). Neither default was changed. Decide on footage from our own camera.

## Performance on the Intel laptop

i9-13900H with Iris Xe, wrist camera at 1280x720, `yoloe-26s` at 384x640 through OpenVINO:

| Run | FPS | Detect | Capture → publish |
|---|---|---|---|
| Headless, machine otherwise idle | 29.5 (camera limit is 30) | 13.5 ms (p95 16.2) | 13.7 ms (p95 16.4) |
| Overlay window + `--record`, idle | 29.5 | 14.9 ms (p95 18.0) | 15.2 ms (p95 18.4) |
| Overlay window + `--record`, Chrome using about 28% of the GPU | 20.5 | 34.6 ms (p95 45.6) | 53.3 ms (p95 74.6) |
| Headless, Chrome using 25 to 50% of the GPU | 22.3 | 42.7 ms (p95 71.5) | 58.8 ms (p95 96.5) |

The integrated GPU is shared with everything on screen, and this laptop also clocks its CPU down under sustained load. A browser playing video or a game in the background can halve the frame rate. For a test that matters, close them and stay on mains power.

If it is still too slow: set `detector.weights.openvino` to `yoloe-26n-seg.pt` (faster, but it misses small and distant objects), or lower `overlay.max_width`.

The message is published before the overlay is drawn, and recording runs on its own thread, so neither adds to guidance latency.

## Layout

```
config.yaml            every threshold and endpoint
paul_vision/
  __main__.py          command line
  app.py               Pipeline (one frame in, one message out), main loop, Recorder
  sources.py           camera (background thread, newest frame, reopen), video file, orientation
  detector.py          Detector interface, profile selection, Ultralytics YOLOE
  bake.py              class list -> prompt embeddings and OpenVINO export
  tracker.py           IoU tracker: stable track IDs, one settled label per object
  selection.py         which track is the target: lock/keep thresholds, switch hysteresis, coasting
  guidance.py          dx/dy smoothing, deadzone, distance hint
  summary.py           spoken sentences: templates, rate limit, settle time
  phase.py             search / approach
  messages.py          the vision message: build, encode, decode, validate
  transport.py         publisher thread with the stall watchdog; Subscriber
  commands.py          the command topic: select_target, clear_target
  stream.py            the camera picture for the web console, MJPEG over HTTP
  overlay.py           debug window
  metrics.py           FPS and per-stage timing
  subscribe_print.py   prints the stream; reference subscriber
scripts/
  list_cameras.py      every camera with its real frame rate and a snapshot
  eval_clips.py        report on recorded clips: status, locks, renames, spoken sentences
tests/                 pure logic plus an end-to-end test over real ZMQ; no camera or GPU needed
sample_videos/         wrist-camera test clips (gitignored; README.md inside lists the sources)
models/                weights and baked artifacts (gitignored)
```

## Troubleshooting

- **`could not bind vision publisher`**: another copy is still running and holds port 5555.
- **`the loaded model detects [...], not the configured [...]`**: run `uv run python -m paul_vision.bake --force`.
- **Wrong camera or no frames**: run `scripts/list_cameras.py` and fix `camera.wrist_index`.
- **Slow**: read the banner (is it the profile you expect?), then the per-stage numbers in the log line. A high `detect` on the laptop usually means something else is using the GPU.
- **Subscriber prints nothing**: it reports after 2 s of silence. Check that the publisher is running and that both use the same `transport.vision_pub`.
