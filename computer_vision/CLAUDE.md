# computer_vision/: PAUL vision module

Camera-based assistance for PAUL: an operator drives a LeRobot SO-101 arm through an IMU armband, and this module watches the wrist camera, picks a target and publishes guidance. Product context is in `../README.md`; how to run and tune this module is in `README.md` here; what is not built or not verified yet is in `NOT_BUILT.md`; the original brief is in `KICKOFF_PROMPT.md`.

## Rules that do not bend

- **Vision assists the operator; it never controls the arm.** It must never open the servo bus.
- Vision never touches audio. It publishes `summary` text; another process speaks it.
- **Never emit misleading guidance.** When unsure, publish `low_confidence` or `no_target`; when the feed stalls, `degraded`.
- Every threshold and tuning value lives in `config.yaml`. None are hardcoded.
- Work only inside `computer_vision/` unless told otherwise.
- Ask before adding a dependency. Do not add `opencv-contrib-python`: it overwrites the `cv2` that Ultralytics depends on, and `opencv-python` 5 already has `cv2.aruco`.
- For Ultralytics, OpenVINO, pyzmq, OpenCV and uv, check current docs through Context7 before writing code against them. Several APIs here differ from older versions (see Gotchas).

## Status

Milestone 1 is built and tested: frame sources, YOLOE detection, tracking, target selection, guidance, spoken summaries, the `vision` publisher with a stall watchdog, the debug overlay, replay and recording.

Built since: the `command` receiver for `select_target` and `clear_target` (`commands.py`), and the camera stream for the web console (`stream.py`). Both were run on 14 November 2026 with `../bridge` and the console in `../frontend`, on the laptop's built-in camera (`--source 0`; the wrist camera was not plugged in): the picture and overlay showed in the console, both commands took effect, and the console recovered when vision was restarted. `README.md` has the three commands under "Web console".

The NVIDIA web-console path was verified on 14 November 2026 on an RTX 2080 Super Max-Q, driver 573.91, using `--extra cu128` (PyTorch 2.11.0+cu128). Camera 0 averaged 25.4 messages/s, with 55 ms median / 74 ms p95 capture-to-subscriber age; camera 1 ran at 10 FPS. Chrome showed the live camera and cup-replay detection overlay, target commands took effect, and picture/telemetry recovered after a vision restart. The frontend rejected zero of more than 3,500 messages. Vision 271, bridge 47 and frontend 246 tests passed; frontend build passed. Full details and local stream-port override are in `README.md`. Arm movement and demo-object accuracy remain unverified.

Everything that is not built (the rest of M2, and M3 to M5) or not verified is listed in `NOT_BUILT.md`, with the place in the code where each piece plugs in. Read it before starting on any of them. When something there gets built or verified, delete it there and describe it here.

## Commands

```powershell
uv sync                                              # once (demo machine: uv sync --extra cu130)
uv run python -m paul_vision.bake                    # once, and after changing detector.classes, imgsz or weights
uv run python -m paul_vision --source wrist          # wrist | <camera index> | <video file>
uv run python -m paul_vision.subscribe_print         # prints what subscribers receive
uv run pytest                                        # 271 tests, about 15 s, no camera or GPU needed
uv run python scripts/eval_clips.py sample_videos    # report on recorded clips
uv run python scripts/list_cameras.py                # find the camera index
```

On NVIDIA machines every `uv run` needs the selected CUDA extra: `--extra cu128` on the tested driver 573.91 machine, or `--extra cu130` for a CUDA 13-compatible driver. A plain `uv run` re-syncs to the CPU build of PyTorch.

Environment: Python 3.12, uv (not a package; run from this folder), Windows. Two detector profiles are chosen at startup and printed in a banner: `torch on CUDA` (demo machine) and `openvino on Intel GPU` (laptop, CPU as fallback). Both use `yoloe-26s`.

## The `vision` message

The publisher binds `tcp://127.0.0.1:5555`; subscribers connect. One ZMQ frame per message: topic, a space, compact JSON. A single frame lets a subscriber set `zmq.CONFLATE`.

```
vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{"track_id":7,"label":"cup","source":"yolo","bbox":[0.41,0.3,0.62,0.58],"confidence":0.91},"guidance":{"dx":-0.12,"dy":0.05,"distance_hint":"near","aligned":false},"summary":"Cup near, slightly left."}
```

| Field | Rule |
|---|---|
| `t` | Capture time of the frame, Unix seconds. Not the publish time. |
| `frame_id` | Sequence number from the source; gaps are skipped frames. |
| `status` | `ok` (target locked), `low_confidence` (something visible, nothing sure enough to lock), `no_target` (nothing visible), `degraded` (no fresh frame; sent by the watchdog every `watchdog.heartbeat_s`). |
| `phase` | M1 emits `search` and `approach`. The contract already allows `grasp`, `carry`, `place`, `release`. |
| `target` | `null` unless `status` is `ok`. `bbox` is `[x1, y1, x2, y2]`, each 0 to 1. `source` is `yolo` (M2 adds `aruco`). |
| `guidance` | `null` unless `status` is `ok`. `dx`, `dy` in −1..1, target centre minus frame centre, positive = right / down in gripper terms. `distance_hint`: `far`, `near`, `in_reach`. `aligned`: inside the deadzone. |
| `summary` | A sentence to speak, or `null` when nothing new is due. |

Consumers act only on `status == "ok"`, and treat silence or a `t` older than 0.5 s as no guidance (the watchdog cannot report its own process dying).

`paul_vision.messages` owns the contract: `build_vision_message`, `encode`, `decode`, `validate_vision`. Change the contract there, then in `tests/test_messages.py`, `README.md` and here.

Other topics (the publisher of each topic binds; endpoints are in `config.yaml`; payloads are in `KICKOFF_PROMPT.md`): `command` on 5557 is consumed by `commands.py`. `pressure` on 5556 and `robot` on 5558 are not consumed yet (see `NOT_BUILT.md`).

## Pipeline

`app.Pipeline.process(frame)` runs one frame through every stage and returns the message:

```
FrameSource -> Detector -> IouTracker -> TargetSelector -> GuidanceFilter -> Summarizer -> build_vision_message
                                                                                               |
                                              VisionPublisher (own thread, owns the socket, watchdog)
```

| Module | Role |
|---|---|
| `types.py` | `Frame(image, t, seq)`, `Detection(label, confidence, bbox, source)`, `Track(track_id, ...)`, `Guidance`, status constants. Boxes are normalized `[x1, y1, x2, y2]` in the oriented frame. |
| `sources.py` | `CameraSource` (background thread, newest frame only, reopens a dead camera after `reopen_after_s`, but allows a newly opened one `first_frame_s`), `VideoFileSource` (paced replay; `exit`, `loop` or `hold` at the end), `orient()`. Orientation is applied to the image here, once, before anything else sees it. |
| `detector.py` | `Detector` protocol: `detect(image) -> list[Detection]`. `resolve_profile` picks torch or openvino; `UltralyticsDetector` runs YOLOE. |
| `bake.py` | Class list to prompt embeddings (`.npz`) and an OpenVINO export, keyed by weights, classes and input size, under `models/`. |
| `tracker.py` | `IouTracker`: greedy IoU match, then nearest centre. Matching ignores the label; a track's label is decided by a confidence vote (`label_memory_s`, `label_margin`). |
| `selection.py` | `TargetSelector`: lock at `conf_lock`, keep at `conf_keep`, switch only to a rival that leads by `switch_margin` for `switch_dwell_s`, coast for `coast_s` over missed frames. `set_preferred_label()` is the hook for the `select_target` command. |
| `guidance.py` | `GuidanceFilter`: offsets, time-constant smoothing (resets when the target changes), deadzone and distance hint with hysteresis. |
| `summary.py` | `Summarizer`: sentence templates; a sentence must be new, stable for `settle_s`, and `min_interval_s` after the last one. |
| `phase.py` | `PhaseMachine`: `search` / `approach`. |
| `transport.py` | `VisionPublisher` (thread, queue, `Watchdog`), `Subscriber` helper. |
| `commands.py` | `CommandReceiver`: subscribes to `command`, polled once per pass of the main loop. `select_target` and `clear_target` call `TargetSelector.set_preferred_label()`; anything it cannot honour exactly is logged and ignored. |
| `stream.py` | The camera picture for the web console, MJPEG over HTTP on `stream.port`. `submit` only swaps a reference; encoding happens on its own thread, once per picture, and only while someone is watching. |
| `overlay.py`, `metrics.py` | Debug window; FPS and per-stage timing. The message is published before the overlay is drawn. |
| `app.py` | `Pipeline`, `Recorder` (encodes on its own thread), `run()` main loop. |
| `config.py` | Frozen dataclasses mirroring `config.yaml`; unknown or missing keys are errors; `validate()` checks ranges. |

All of tracker, selection, guidance, summary, phase and messages are pure (no I/O, time passed in as `t`), which is why they can be tested and replayed deterministically.

## Adding or changing a config key

1. `config.yaml`: the key with a comment that says what it does.
2. `paul_vision/config.py`: the dataclass field, and a range check in `validate()` if it has one.
3. Tests: each logic test file builds its own config object (`CFG = TrackerConfig(...)` and so on), so tuning `config.yaml` never breaks a test. Add the field there, and a test for the behaviour. The end-to-end, overlay and config tests load the shipped `config.yaml`.

## Testing

- `uv run pytest`: pure logic per module, plus `test_pipeline_e2e.py` (a fake detector through the real pipeline and real ZMQ).
- `scripts/eval_clips.py <clips or folders>`: runs the real detector and pipeline over recorded clips, every frame, on the clip's own clock, so results do not depend on machine speed. Prints per clip: status shares, target labels, locks and renames, and every sentence with its time. `--sheets DIR` saves annotated stills. Run it before and after changing a threshold or the class list.
- `sample_videos/`: 17 wrist-camera clips from public SO-100/SO-101 datasets (not in git; sources in `sample_videos/README.md`). They are not our camera or our objects. Record our own with `--record` once the camera is on the arm.

What the sample clips showed (numbers in `README.md`):
- An upright cup seen from the front works well: one steady lock, the right name, sensible sentences.
- The same object gets different labels from different sides. A pill bottle is "bottle" from the side and "cup" cap-first; "pill bottle" is almost never the label. The label vote reduces renames; it cannot make the name right.
- Objects that fill the frame, cups lying on their side under a tilted camera, small blocks and trays, and dark or blurred scenes are detected poorly or not at all.

## Gotchas

- **Bake before first run and after changing classes, weights or `imgsz`.** The first bake downloads weights (30 MB) and a text encoder (243 MB) and needs git for the `clip` package. Runtime then needs no network. A mismatch between the loaded model and `detector.classes` stops startup with the fix in the message.
- **Ultralytics sets OpenCV to one thread when imported.** `UltralyticsDetector` restores the default after warm-up (`cv2.setNumThreads(-1)`); without it the overlay resize and video writing are several times slower.
- **Half precision is `quantize=16`** in this Ultralytics version, not `half=True`.
- **YOLOE checkpoints are `-seg` models.** `bake.py` loads them with a detection head so no masks are computed.
- **Camera format order on Windows.** With DirectShow, set the resolution first and MJPG after it, or the driver falls back to YUY2 at 10 FPS. `sources.open_capture` does this and logs the format it got.
- **A camera's first frame can take over a second.** The laptop's built-in camera needs 1.1 s. Reopening it after `reopen_after_s` (1.0 s) never let it start, which is why `camera.first_frame_s` exists and must not be below `reopen_after_s`.
- **The integrated GPU is shared.** A browser playing video can halve the frame rate on the laptop. Close it for any measurement.
- **Time.** Stamp frames with `clock.now()`, not `time.time()`: before Python 3.13, `time.time()` on Windows only ticks every 15.6 ms. `clock.now()` is Unix time with sub-millisecond resolution.
- **The publisher thread owns the socket.** Other threads call `publish()`, which only queues.
- **No git commits from here without being asked.** The repo is shared with the team.

## Neighbouring components

- `../bno085_reader/`: ESP32 firmware that outputs BNO085 quaternions as `w,x,y,z` CSV at 50 Hz over serial (115200 baud).
- Robot control: LeRobot SO-101 with STS3215 servos.
- Haptics: coin vibration motors on DRV2605L drivers, fed by `guidance`.
- Voice: speaks `summary`.
- Recording and the live UI consume the `vision` stream alongside the sensor data.
