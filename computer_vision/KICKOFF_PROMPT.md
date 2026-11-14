# Kickoff prompt: PAUL computer vision module

Copy everything below the line into a fresh Claude Code session started at the repo root.

Milestone 1 is built. The table and contracts below were corrected to match what was built; `README.md` and `CLAUDE.md` in this folder describe the result.

---

You are building the computer vision module for **PAUL**, a hackathon project: an IMU-armband teleoperation system for a LeRobot SO-101 arm with haptic feedback. Read `README.md` and `computer_vision/CLAUDE.md` first for product context. Work only inside `computer_vision/` unless told otherwise.

**Vision assists the operator; it never controls the arm.** It finds objects in the wrist-camera feed, picks a target, and publishes guidance that other processes turn into haptic pulses (DRV2605L) and speech (TTS).

## Decisions already made (do not re-litigate; ask me before deviating)

| Area | Decision |
|---|---|
| Language / env | Python 3.12, managed with **uv** (`pyproject.toml` + lockfile). **Two detector profiles, chosen at startup:** the demo machine is Windows 11 with an **NVIDIA GPU** (CUDA build of PyTorch, `uv run --extra cu130 ...`); the development laptop has an Intel GPU and runs an **OpenVINO** export. |
| Camera | A single **wrist camera** (eye-in-hand) on the gripper. Guidance means "move so the target is centered in the frame". |
| Detection | **Ultralytics YOLOE-26** (`yoloe-26s`), the open-vocabulary YOLO. It runs classes by text prompt now and later supports user-tagged items (text and visual prompts). The class list is turned into prompt embeddings once, by `python -m paul_vision.bake`, so startup needs no network. Hidden behind a `Detector` interface. |
| Stations | **ArUco markers** (OpenCV) mark the pick and drop-off stations. Implement these in Milestone 2. |
| Target selection | **Auto-pick by default.** Later a voice command can override the target, arriving as a message on the command topic. Vision never touches audio. |
| Distance | **Bbox-size heuristic.** The fraction of the frame the box fills maps to `far / near / in_reach`, with per-label thresholds in config. |
| Spoken summary | **Templates** ("Cup ahead, slightly left."), rate-limited, emitted only when the content changes. No LLM. |
| Transport | **ZMQ PUB/SUB** (pyzmq) between Python processes, plus a small **ZMQ→WebSocket bridge** for the web UI (Milestone 3). Each message is **one ZMQ frame**: the topic, a space, then compact JSON (so a subscriber can use `zmq.CONFLATE`). The publisher binds; subscribers connect. Every payload carries a timestamp `t`. |
| Task phases | A **simple state machine** driven by vision plus the **gripper pressure sensors** (Milestone 2). |
| Pressure sensors | 4 thin-film sensors on a **separate microcontroller** that streams CSV over USB serial. A small bridge script reads the serial port and publishes the `pressure` topic. Use a mock publisher until the hardware is ready. |
| Servo data | STS3215 position and load are owned by the arm-controller process, because only one process can open the servo bus. Vision may subscribe to a `robot` topic later, and must never open the servo bus. |
| STT | Cloud STT, in a separate process (Milestone 5). It publishes `command` messages. |
| Performance | **≥20 FPS** end-to-end, from frame capture to published message. Log FPS and per-stage latency. Measured on the Intel laptop: 29.5 FPS, 14 ms from capture to publish. Not yet measured on the NVIDIA machine. |
| Dev workflow | **Replay mode:** run from a video file or laptop webcam without the arm. Also an **OpenCV debug overlay** window and **pytest** tests for the pure logic. |

When you use Ultralytics (YOLOE `set_classes`, visual prompts, `save_prompt_embeddings`, export), pyzmq, OpenCV ArUco (the `ArucoDetector` API changed in 4.7+), or the WebSocket library, **check current docs through Context7** instead of relying on memory.

## Message contracts (ZMQ, one frame per message: `<topic> <JSON>`)

All endpoints are in `config.yaml`. Defaults: vision binds `tcp://127.0.0.1:5555`; it will subscribe to `pressure` on 5556, `command` on 5557 and `robot` on 5558, where the publisher of each topic binds.

**`vision`** (published by this module every frame):
```json
{
  "t": 1731542400.123,
  "frame_id": 1042,
  "status": "ok",
  "phase": "approach",
  "target": {"track_id": 7, "label": "cup", "source": "yolo", "bbox": [0.41, 0.30, 0.62, 0.58], "confidence": 0.91},
  "guidance": {"dx": -0.12, "dy": 0.05, "distance_hint": "near", "aligned": false},
  "summary": "Cup ahead, slightly left."
}
```
- `t`: capture time of the frame in Unix seconds, not the time of publishing.
- `status`: `ok | no_target | low_confidence | degraded`. When it is not `ok`, `target` and `guidance` are `null`. **Never emit misleading guidance.** `low_confidence` means something is visible but below the confidence needed to lock; `degraded` is sent by a watchdog every 0.5 s while no fresh frame arrives.
- **Consumers treat silence, or a `t` older than 0.5 s, as no guidance.** The watchdog cannot report its own process dying.
- `bbox`: normalized `[x1, y1, x2, y2]` in the range 0–1.
- `dx`, `dy`: in the range −1..1, target center minus image center. Positive means right or down **in gripper terms**. `camera.rotate_deg`, `flip_h` and `flip_v` turn the image itself before detection, so the overlay, `bbox` and `dx`/`dy` share one frame however the camera is mounted.
- `aligned`: true inside a configurable deadzone.
- `summary`: `null` unless a new utterance is due.

**`pressure`** (consumed; published by the pressure bridge or the mock): `{"t": ..., "raw": [r0, r1, r2, r3], "norm": [0..1 x4]}`

**`command`** (consumed; from UI or voice): `{"t": ..., "type": "select_target", "label": "pill bottle"}`, `{"type": "clear_target"}`, `{"type": "set_phase", "phase": "search"}`. Later: `{"type": "tag_text", ...}` and `{"type": "tag_visual", "bbox": [...], "name": ...}`.

## Milestone 1: built

1. Scaffold a uv project in `computer_vision/`: a package (e.g. `paul_vision/`), `config.yaml`, `tests/`, and a short `README.md` covering how to run it.
2. **Frame source:** webcam index, video file (replay mode), or the wrist camera, chosen by CLI flag or config.
3. **Detector:** YOLOE with configurable text classes, a confidence threshold, and GPU device selection. A small IoU tracker of our own (`tracker.py`) keeps the target ID stable across frames; Ultralytics tracking is not used, because it installs a package at runtime and would not cover ArUco markers.
4. **Target selection:** auto-pick by confidence × centeredness, with hysteresis so the target doesn't flicker between objects.
5. **Guidance:** compute `dx` and `dy` with EMA smoothing, a deadzone that sets `aligned`, and the bbox-area distance hint.
6. **Summary:** template generator, rate-limited and deduplicated.
7. **ZMQ publisher** for the `vision` topic, plus a tiny `subscribe_print` script to view the stream.
8. **Debug overlay:** draw the boxes, the highlighted target, a guidance arrow, the status, and FPS/latency.
9. **Graceful degradation:** publish `no_target` or `low_confidence` explicitly. A watchdog emits `degraded` if frames stall.
10. **Tests:** cover offset math (including rotation and flips), target selection with hysteresis, distance thresholds, and summary rate-limiting. Keep these as pure functions with no camera or GPU needed.

Done when: `uv run python -m paul_vision --source <video_or_webcam>` shows the overlay at ≥20 FPS, a second terminal receives valid `vision` messages, and `uv run pytest` passes.

## Later milestones (design for these, don't build them yet)

M2 to M5 are listed in `NOT_BUILT.md` in this folder, with what each has to do, what already exists and where it plugs into the code. In short: M2 is ArUco stations, the rest of the commands, pressure input and the full phase machine; M3 is the ZMQ→WebSocket bridge for the live UI; M4 is user labelling of objects; M5 is a cloud STT process that publishes `select_target` commands.

## How to work

- Start by proposing the file layout and module boundaries in a few lines, then implement Milestone 1.
- Keep it narrow and reliable over general. This is a structured demo workspace.
- Keep every threshold and tuning value in `config.yaml`, never hardcoded.
- Update `computer_vision/CLAUDE.md` with the final interfaces once Milestone 1 works.
- Ask me before adding dependencies beyond ultralytics, torch, opencv-python, openvino, pyzmq, numpy, pyyaml, pytest, and `clip` from github.com/ultralytics/CLIP (needed only by the bake step). Do not add `opencv-contrib-python`: it overwrites the `cv2` that Ultralytics depends on, and `opencv-python` 5 already includes `cv2.aruco`.
