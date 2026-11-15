# PAUL vision: not built, not verified

What this module still lacks: features that are planned but do not exist, and things that exist but have not been checked on the real setup. What is built is described in `README.md` (running and tuning) and `CLAUDE.md` (how the code fits together). The original brief is `KICKOFF_PROMPT.md`.

This is the state of the code on the evening of 14 November 2026. That day milestones 4 (user labelling) and 5 (voice commands) were built and the station markers were changed. In the evening came the grip from the gripper servo's load, the console's `alert` and label controls (in `../frontend`), and a live run through the team launcher `../demo.ps1` on the laptop. When an item is built or verified, delete it here and describe it in `README.md` and `CLAUDE.md`.

## At a glance

| Milestone | Item | State |
|---|---|---|
| M2 | ArUco stations | built; never seen a printed marker or the wrist camera; tilted markers read further, and a marker at the frame edge can be missed |
| M2 | Commands: `select_target`, `clear_target`, `set_phase`, and `send_command` | built; `set_phase` has gone through the bridge as the console sends it, but the console's own phase control has not been clicked |
| M2 | Pressure input, mock publisher, serial bridge | built; no real pads, line format assumed, not calibrated, pyserial not installed |
| M2 | All six phases and the `dropped` alert | built; run with the mock pads only (also through the launcher, where the alert reached the console's WebSocket feed); `release` only after `set_phase place`, the blind spot only in tests |
| M2 | Grip from the gripper servo's load (`robot` topic, `gripper_load`) | built and tested offline; shipped off (`gripper_load.enabled: false`) with placeholder thresholds; never run with the arm |
| M3 | ZMQ to WebSocket bridge | built in `../bridge`; run with vision and the web console, also through `../demo.ps1`; what those runs did not cover is below |
| M4 | User labelling of objects (`tag_visual`, `tag_text`, `untag`) | built; run on recorded clips and live on the laptop's built-in camera through the launcher (OpenVINO on the Intel GPU; a tag survived a restart); never on the CUDA machine or the wrist camera |
| M5 | Voice commands (speech to text) | voice command process built; run with typed lines, also beside the launcher's bridge and console; `--listen` started once on the laptop microphone, but no command has been spoken to it; the team's speech recognizer not chosen |
| | Console: the `dropped` alert, the label controls, a box drawn on the picture | built in `../frontend`; a box drawn on the camera view was learned; the alert on screen and the "Names" buttons not checked in a browser (see "Outside this folder") |
| | Haptics acting on `alert`; spoken feedback from the voice process | not built (outside this folder) |
| | One water bottle as two tracks on PyTorch (`recordings/run1.mp4`) | not fixed |
| | CUDA profile on the NVIDIA machine | run with M1 and the web console only (`--extra cu128`, see `README.md`); never with stations, labels or voice |
| | Camera orientation on the arm, class list, thresholds | not checked on our setup |
| | Known limits of what is built: phases, the pad bridge, labels, voice, the camera index, constants in the code | not handled; see "Known limits" |

The milestones depend on each other in this order: M4 and M5 build on the commands of M2 (M4 adds three, M5 sends them), and the console's label controls go through the bridge (M3).

## M2: built, not verified on the hardware

Everything in milestone 2 has run in the tests and on a bench (a synthetic marker clip or a looped still clip, the mock pads, `send_command`). None of it has run on the arm, the real pads or a printed station marker.

### Pads

- **Real pads and their thresholds.** Every value under `pressure:` in `config.yaml` is unchecked on the hardware: `combine`, `on_threshold` / `on_hold_s`, `off_threshold` / `off_hold_s`, `stale_s`. They are read by `pressure.ContactDetector`. Set them from recorded grasps (`pressure_bridge --log`, then `mock_pressure --replay`), as `README.md` describes under "Calibration".
- **The serial line format is assumed:** one line per sample, four raw readings in pad order, separated by commas or spaces. If the firmware sends something else, change `pressure_bridge.parse_line` and its tests in `tests/test_pressure_bridge.py`. `pressure.bridge.baud` (115200) must match `Serial.begin()`. The pad order (left tip, left base, right tip, right base) is the console's and matters for `combine: both_fingers`.
- **Calibration.** `pressure.calibration` ships as the 12-bit ADC range, which is no calibration. It is applied in `PressureBridge` (through `pressure.normalize`), so the bridge has to be restarted after a change. Procedure in `README.md` under "Calibration".
- **pyserial.** Not installed and not a dependency. Without it the bridge prints the fix and exits with status 3; it has never opened a real port. Adding it (`uv add pyserial`) awaits the team's decision. It is imported in `pressure_bridge._serial()`, used by `open_serial_port()`.

### Stations

- **A real printed marker on the wrist camera.** Markers have only been seen on synthetic frames and a synthetic 1280x720 clip. Not checked: the reading distance, the slant, glare and motion blur on the real camera and a real print. The settings are under `stations:` in `config.yaml`; the printing advice is in `README.md` under "Station markers".
- **The field of view behind the station distance thresholds.** The `pick station` and `drop station` entries in `distance.thresholds` (`near` 0.03, `in_reach` 0.12) are computed for a 100 mm marker and a 90° horizontal field of view, not measured. `place` begins at `phase.place_hint` (`near`), about 0.36 m from the marker under those assumptions. Measure the field of view, or the box area at the distance where `place` should begin, and set both.
- **ArUco cost per frame.** On the Intel laptop, headless, a 1280x720 marker clip paced at 30 FPS, 450 frames, three runs: 28.5 FPS with stations on against 30.0 off, `detect` 29.8 ms against 21.6 ms, of which `aruco` 8.0 to 8.2 ms. Not tried: running `StationDetector.detect` on its own thread beside YOLOE (in `detector.CompositeDetector`), or `stations.detect_max_px: 960` (about 1 ms less, but markers have to be 1.5 times bigger at a slant). With a mirrored picture (exactly one of `camera.flip_h` and `flip_v` set) every frame is searched twice, which takes about twice as long: 4.9 to 5.0 ms against 2.4 to 3.2 ms on one synthetic 1280x720 frame. Not measured in the pipeline.
- **Tilted markers read further than they are.** A station's box is a square of the marker's mean side, centred on the marker (`aruco.square_box`), so turning a marker in the picture no longer changes its distance hint, but tilting it away from the camera shortens its sides. At the frame centre it reads up to 6 % further at 30° of tilt, 13 % at 45° and 22 % at 60°, and never more than 3 % closer; on renders at 60°, `near`, and with it `place`, came on at about 0.28 m instead of 0.36 m. Seen on synthetic renders only, not on a real print.
- **Markers at the frame edge.** On synthetic frames OpenCV missed a marker whose white margin ended a few pixels from the frame edge, depending on where it fell within a pixel. Setting the detector parameter `minDistanceToBorder` to 0, or padding the frame, made it detect. Not done: any change to the detector parameters needs the check on 17,501 frames without markers (`README.md`, "False stations") run again. Until then `README.md` advises keeping the marker and its margin a little inside the picture.
- **The pick station** is found and drawn in the overlay, but no phase uses it.

### Phases and commands

- **`set_phase` from the console's own control.** On 14 November (evening) `set_phase carry` went through the launcher's bridge to `tcp://127.0.0.1:5557` from a WebSocket client that sends what the console sends (`{"t": ..., "type": "set_phase", "phase": "carry"}`, with the console's `Origin`). Vision applied it, and the bridge logged `command relayed`. Nobody clicked the phase control on the console's Signals page (`../frontend/src/routes/signals/CommandPlate.tsx`).
- **Release and the blind spot.** The blind spot that keeps `approach` while the gripper closes has only run in `tests/test_phase.py` and `tests/test_pipeline_e2e.py`, not on the bench. The `release` phase with "Released." ran once on the bench, through the real processes with the mock pads and a looped still clip, after `send_command set_phase place` (`README.md`, "A bench run without hardware"); it has not run after a `place` reached on a marker.
- **Phase timings** (`phase:` in `config.yaml`: `grasp_settle_s`, `place_dwell_s`, `leave_place_dwell_s`, `release_hold_s`, `sensor_loss_hold_s`, `blind_spot_s`) have not been tried on a real grasp.

### Grip from the gripper servo's load: built, never run with the arm

Nothing publishes `pressure` yet (no pads), so without the mock the phase never passed `approach`. `gripper_load.py` decides contact from the gripper servo's load instead. The load comes in the arm controller's `robot` messages (`../leslider_control/telemetry.py`, 10 Hz on `transport.robot_sub`, port 5558), and the decision uses the same `ContactDetector` as the pads (`README.md`, "Gripper load"). Vision only subscribes; it never opens the servo bus.

What has been tested: the parsing, the receiver on real ZMQ, the decision at 10 Hz, a grasp, carry and drop through `run()` with fake `robot` messages, and the parsing against `telemetry.robot_message` itself.

It ships off (`gripper_load.enabled: false`), because nothing about the real servo is known yet:

- **Whether the load tells holding from not holding at all.** Not measured. The arm was not connected. A servo pressing on an object should show load, but a servo that is moving, or pressing one jaw on the other, may show load too. Recording grasps needs a person at the arm:
  1. The user runs the full `../demo.ps1`, which starts the controller.
  2. In this folder, record with `uv run python -m paul_vision.subscribe_print --topic robot --raw | Out-File -Encoding utf8 recordings\robot-grasps-<date>.log`.
  3. Grasp each demo object, close on nothing, and move freely while holding something.
- **`sign`, `on_threshold`, `off_threshold` and `empty_pos` are placeholders** (1, 0.25, 0.12, null). Set them from that log. Then add a test that replays the log through `reading_from_robot` and `ContactDetector` and counts the grasps and releases. Only then set `enabled: true`, and run a let-go during `carry` with the arm.
- **Slower than the pads.** At 10 Hz with `on_hold_s` 0.2 s and `off_hold_s` 0.3 s, contact comes on about 0.2 to 0.3 s after the load rises and goes off 0.3 to 0.4 s after it falls; a drop is reported that much later.
- **One grip source at a time.** With `enabled: true` vision does not read the `pressure` topic at all. The pads and the load are not combined.
- **The controller's clock.** Staleness is judged on the controller's `t` (`time.time()`, stamped just before the bus read) against vision's `clock.now()`. That works only on one computer. Before Python 3.13, `time.time()` on Windows ticks every 15.6 ms, which is small against `stale_s` (0.35 s).
- **The launcher's `--robot-pub-port`.** `../leslider_control/session.py` passes another port to the controller and the bridge, but not into the session's `vision.yaml`, so vision keeps 5558 and hears nothing. Contact then stays unknown, never wrong. A controller `--dry-run` publishes no load (`feedback: preview`), which has the same result.

## M3: ZMQ to WebSocket bridge: built, not checked on the wrist camera

The bridge is in `../bridge` (its `README.md` says how to run it and what it accepts), and the vision side is `paul_vision/stream.py` and `paul_vision/commands.py`. All three were run together with the web console on 14 November; `README.md` here describes that run under "Web console". Not covered by it:

- **The wrist camera.** It was not plugged in. The picture came from the laptop's built-in camera (`--source 0`). The same applied in the launcher run that evening, where `--source wrist` opened the built-in camera (see "Known limits", "Camera").
- **Sustained frame rate on the final setup.** On the laptop, in the one run where the built-in camera delivered 30 FPS, vision held 29 to 30 FPS with the console watching; `detect` rose from about 15 ms to about 26 ms while headless Chrome was running on the same integrated GPU. The NVIDIA machine has since been tested with ordinary Chrome: camera 0 averaged 25.4 messages/s and camera 1 ran at 10 FPS (short runs; see `README.md`). Neither run had the station markers on. A long run with the final camera, lighting and other demo processes remains to be measured.
- **How far the overlay and the picture are out of step.** They reach the console separately (WebSocket and MJPEG). Not measured on a moving object.
- **The camera choice in a browser since connections are closed in stages.** That run was before `stream.py` began to close each connection in stages (`_Server.shutdown_request`: end the answer, read what the client still sends for at most 2 s, then close). Since then the camera server has run in the tests, once on the laptop with Python's `http.client` and raw sockets, and once with a browser through the launcher on 14 November (evening): the console's viewer connected at 22:09:02 and stayed for the session (`README.md`, "Web console"). Vision logged no camera switch in that session, so `POST /source` has not been used from a browser since the change, and whether the console's camera list (`GET /sources`) loaded was not checked.

## Outside this folder

Changes this module's contract needs elsewhere, and what was built there but not checked.

- **The console's alert and label controls, in a browser.** Built in `../frontend` on 14 November (evening):
  - **The alert.** `alert: "dropped"` raises "Object may have dropped" (`src/app/derive.ts`; tests in `src/app/derive.test.ts`).
  - **The "Names" group.** On the Signals page it sends `tag_visual` for the locked target, `tag_text`, `untag`, and `select_target` for a tag.
  - **Draw box.** Under the camera view, **Draw box** lets the operator draw a box on the picture and sends `tag_visual` with it (`src/routes/console/TagBox.tsx`).

  Checked:
  - the unit tests;
  - through the launcher's bridge, from a WebSocket client that sends what the console sends: each label command, and a let-go in `carry` whose `dropped` alert arrived on 15 messages;
  - in a browser, a box drawn on the camera view, learned as "face" and live 33.5 s later.

  Not checked in a browser: the alert on screen, and the "Names" buttons.
- **Stations in the console.** The console should expect `source: "aruco"` and the label `drop station` on the target in `carry` and `place` (`pick station` is never a target). Its `VisionTarget.source` is a plain string, so nothing breaks; how the console shows these labels has not been checked.
- **No haptics consumer of `alert`.** Nothing turns `alert: "dropped"` into a haptic alarm yet. The spoken side is covered: the same message carries "Grip lost. Object may have dropped." in `summary`.
- **The voice process's feedback is printed only.** `python -m paul_vision.voice` prints what it understood and sent, or why it sent nothing, in its own window. Nothing speaks that to the operator, and it cannot tell whether vision accepted a label command. Vision's answers, refusals included, are in `summary`, which the team's speech output is to speak.

## One water bottle, two tracks

On the PyTorch profile on the CPU, a target on the water bottle in `recordings/run1.mp4` alternates between two tracks. YOLOE returns a second, partial box of the same bottle that survives non-maximum suppression (its IoU with the full box is 0.478, just under `detector.iou` 0.5), so the tracker (`tracker.py`) opens a second track for it and the target switches between the two. Tags do not cause it: the detections are the same with and without a tag, and it also happens with an empty label library. Not fixed. Two fixes have been suggested and neither has been tried: a duplicate check in the tracker that drops a box lying mostly inside another box of the same label, or a slightly lower `detector.iou`. Either needs `scripts/eval_clips.py` on the sample clips afterwards, since both change what the tracker sees.

## M4: user labelling of objects: built, run on clips and the laptop camera

`tag_visual`, `tag_text` and `untag`, the label library in `labels/`, the label worker and the offline commands (`python -m paul_vision.labels`) are built; `README.md` describes them under "Teaching vision a name". They were run on 14 November 2026 on the development laptop, on loops cut from `recordings/run1.mp4`, with OpenVINO on the Intel GPU and PyTorch on the CPU. That evening they ran live through the launcher (`../demo.ps1 -ServicesOnly`), on the laptop's built-in camera with OpenVINO. Tags were taught from `send_command`, typed voice lines and the console, and a tag survived a restart. The times and what was seen are in that section under "What has been run". Not run, or not built:

- **The CUDA profile.** The class swap on a running predictor (`UltralyticsDetector.adopt`) with PyTorch on the NVIDIA GPU and `quantize=16` (half precision) is written for and never run; the CUDA profile itself has run there, but only before M4 (see "Not verified"). The first run on the demo machine should teach one tag and check the log line `label change ... live after ...`.
- **The wrist camera.** Only recorded clips and the laptop's built-in camera were used.
- **Only in unit tests:** how well a text tag (`tag_text`, for instance "white mug" or "black energy drink can") picks out its object compared with the configured classes; the rebuild of a stale library at startup after a change of weights or `detector.imgsz`; the two timeouts (`labels.worker_timeout_s`, `labels.load_timeout_s`); and teaching from a coasting target's last sighting (in a live run only its refusal, "the target is out of view.", was heard); saving a live change again after the rename to `library.json` failed, before the next change and at stop (the Windows lock was simulated by replacing `labels.commit_next`, not made with a real open file); and how an unexpected error in `teach()` or `poll()` ends the change as failed.
- **`untag` on PyTorch** was not timed.
- **Similar objects in general.** On the `run1.mp4` loops a picture of the flask kept the target off the water bottle next to it. That is one clip with one look-alike, not proof that a picture tells similar objects apart.
- **A worker that stays running.** Each change starts a new worker process. On PyTorch, 4.2 s of the worker's 7.2 s went to starting the process and 1.0 s to loading the model. Live on OpenVINO through the launcher, changes took 29.7 to 48.7 s, against 15.7 to 24.8 s on the recorded clips. Of each worker's run, 7.9 to 10.9 s went to starting the process, 2.6 to 4.8 s to loading the model, 11.1 to 14.0 s to a text embedding (1.7 s for a picture) and 12.4 to 17.0 s to the OpenVINO export. A worker kept alive between changes could save the first two, and the text encoder's load inside the embedding. Not built. Why the live changes were slower than on the clips was not established; the camera feed and the console were running beside them.
- **A lock between `labels remove` and a running vision.** Only one process may change a label library, and nothing enforces it. `remove` and `rebuild` write `library.json` without checking whether vision runs, and both delete `library.next.json`, which may hold a change the running vision has not saved; while it runs, `untag` has to be used instead (`README.md`, "Offline commands"). A second vision started on the same `labels.dir` is not detected either.

## M5: voice commands: built; the team's recognizer is not

`python -m paul_vision.voice` turns utterances, as text, into commands; `README.md` describes it under "Voice commands". On 14 November 2026 on the laptop it ran against vision on a looped sample clip with typed lines, and every command type took effect, the three label commands included. That evening it ran with typed lines beside the launcher's vision, bridge and console, on the laptop's built-in camera (`README.md`, "What has been run" under "Voice commands"). Vision never touches audio; it only receives the commands. Not built or never run:

- **The team's speech recognizer.** Not chosen (cloud or other) and not connected. It plugs in by writing one utterance per line into `python -m paul_vision.voice`.
- **Commands spoken to `--listen`.** The stand-in has been started once on the laptop's microphone, for 20 s. It heard three bits of room speech at confidence 0.01 to 0.12 and sent nothing. No command has been spoken to it. Still to do: 20 commands and 10 phrases that are not commands, said by a person at the microphone, counting misses and wrong sends. Real voices, accents, babble and the noise of the demo hall are untested. Its numbers come from loopback (`scripts/voice_loopback.py`): synthetic speech, with white noise at most.
- **Rejecting sound-alikes under `--listen`.** `voice.listen.decoy_words` (`[bowl, paula]`) puts listed sound-alikes in the grammar, never sent. In the loopback test it took the wrong sends from 13 to 7 of 102 phrases that are not commands: "bowl" as bottle from 3 to 0, "paula" as paul from 6 to 3. "cap" is still heard as cup (4 times); as a decoy word it turned 6 of 54 correctly spoken "cup" commands into "cap", so it is not in the list. A sound-alike that is not listed ("cop", "cub", "books") is still heard as the label: in a second phrase set, most of whose 96 phrases that are not commands hold such words, 45 were sent as commands (52 without `decoy_words`). No threshold in `voice.listen` removes the rest. Loopback only, never with a microphone.
- **Dictated tag names.** Off by default (`voice.listen.dictated_names: false`): 2 of 12 names came back wrong at confidence 0.95 or more. New names are typed into the same window for now.
- **Spoken feedback.** See "Outside this folder".
- **The demo machine.** Voice has run on the laptop only.

## Known limits

What is built behaves as below. None of it is handled yet; each needs a decision or a change.

### Phases

- **The placed object can be the next target at once.** From `release` on, objects are candidates again (`phase.target_domain`), and nothing leaves out the one just put down: it can be locked in `release`, and once `phase.release_hold_s` (1.0 s) has passed it is the target of `approach`. Whether it should be left out, and for how long, is a product decision the team has not made.
- **A fall during `place` is a release, not a drop.** `place` is kept until the drop station is seen at `phase.leave_place_hint` (`far`) or further for `leave_place_dwell_s` (1.0 s); with the station out of sight it stays. Contact that ends while the phase is still `place` is always "Released.", with `alert` null, also when the object fell after the gripper turned away from the station.
- **A drop and a new grip during a camera stall are not seen.** The phase machine takes the pad state only with a frame. If the object drops and is gripped again while no frames come, the first frame afterwards shows contact held: the phase stays, with no `dropped` alert and no "Grip lost. Object may have dropped.".
- **A camera switch resets the phase.** Choosing another camera in the web console resets the phase machine, also in the middle of a `carry`: the phase goes back to `search` and a `dropped` alert being held ends. With the pads still holding, the next frame starts a new `grasp`, and "Grip established." is said again after `phase.grasp_settle_s` (`README.md`, "Phases").

### Pads

- **The bridge trusts `--port`.** `pressure_bridge` takes any line of four numbers from the port it is given as pad readings and publishes it. Nothing identifies the pad board. The armband's firmware (`../bno085_reader/`) prints its quaternions as `w,x,y,z` lines at 115200 baud, the bridge's own format and speed, so with the armband's port given by mistake, the bridge would publish its orientation as pad readings, and vision would decide contact from it.

### Labels

- **A removed name lingers for a moment.** After "Forgot X.", the target can still be published as X for at most `selection.coast_s` (0.15 s): while it is missed and coasting, the selector holds the target as it was last seen, label included.
- **A tag can lose to a configured class on the same object.** On PyTorch on the CPU, "blue cup" taught on an object of `sample_videos/wrist_so101_bottle_1.mp4` (frame 90) that the model scored 0.84 as `cup` did not take the label. On OpenVINO, "my flask" (taught with a box, then a second view by voice) was the target's label in 993 of 1729 messages of one run. Not measured in general.
- **Answers during a stall or pause are only logged.** An answer due while the feed is stalled or paused for more than `watchdog.stall_timeout_s` (0.5 s) is not sent (`README.md`, "Teaching vision a name"). That includes "X is ready." for a change that goes live during the stall: the operator hears nothing, and the log has `announcement dropped, ...`.
- **A lost staged file blocks label changes.** If `library.next.json` disappears while a change is live but not saved (deleted by hand, or by `labels remove` or `rebuild`), every new change is refused ("... could not be saved yet.") until vision restarts, and the change is lost then. If writing a job fails after its picture (`.npy`) was written, that file stays in `labels/jobs/`.

### Voice

- **"Sent" counts any subscriber.** "sent to N subscribers" counts every subscription to `command` on `voice.endpoint`, also one to every topic, from any program. With vision stopped and `subscribe_print --topic command` running, a command is printed as sent to 1 subscriber and vision never gets it. `send_command` counts the same way.
- **`--listen` is deaf for about 1 s after the tags change.** `run_listen` stops the recognizer and starts a new one with the new grammar; speech in that time is not heard and not reported.
- **A hard kill can leave `powershell.exe` running.** The stand-in recognizer is a child process (`subprocess.Popen`, no job object). Voice stops it when it ends normally, Ctrl+C included, but killing voice outright (Task Manager, `taskkill /F`) does not. It probably ends at its next recognition, when writing to the closed pipe fails and `scripts/voice_listen.ps1` exits; that was read in the script, not tried.
- **A late command is still applied.** Vision never reads a command's `t`, so a command that reaches it late, for example after voice waited up to `voice.deliver_wait_s` (2 s) for vision to subscribe, is applied as if it were new.
- **"paul this is wrong" teaches "wrong".** Whatever follows "this is" is taken as a new name unless it points ("it", "that one") or is one of a few words that are not names ("one", "something", "nothing", "everything", "anything", ...). A spoken correction is therefore sent as `tag_visual` for a tag called "wrong".
- **A tag that starts with "my" or "the" can be refused.** The `--listen` grammar has an optional "the" or "my" between the select verb and the label, so "find my flask" can come back as "find the my flask" or "find my my flask". Those score 0.80 and 0.84 against "my flask" and are refused at `voice.label_cutoff` (0.9): nothing wrong is sent, but the command is not carried out, and it has to be said again.

### Camera

- **`--source wrist` opens whatever camera has the index `camera.wrist_index`.** `config.yaml` has 0, the wrist camera on the machine it was set on. Windows numbers cameras in the order it finds them. On the evening of 14 November the wrist camera was unplugged, so index 0 was the laptop's built-in camera, and `../demo.ps1` (which passes `--source wrist`) ran on it. Nothing warned; the log says only `source: camera 0`. Vision does not check the camera's name. Check the picture, or run `scripts/list_cameras.py`, before a demo.

### Constants in the code

These are module constants, not keys in `config.yaml`; the team may want some of them there:

- `label_manager.KEEP_LOGS` (10): worker logs kept in `labels/jobs/`.
- `label_manager.SHUTDOWN_WAIT_S` (5.0 s): how long a stopping vision waits for a killed worker or a loading thread.
- `labels.REPLACE_TRIES` (5) and `labels.REPLACE_PAUSE_S` (0.01 s): tries at replacing a file that another process has open on Windows, such as `library.json`.
- `PressureReceiver._MAX_PER_POLL` (1024) and `_WARN_EVERY_S` (5.0 s) in `pressure.py`: readings taken per pass at most, and the interval between summaries of invalid pressure messages.
- `stream._SEND_TIMEOUT_S` (5.0 s): the camera server drops a client that sends no request, or a viewer that accepts no data, for this long.
- `stream._LINGER_S` (2.0 s) and `stream._LINGER_MAX_BYTES` (64 KiB): after its answer, how long the camera server waits for the client to close its end, and how much of what the client still sends it reads and drops, before it closes the connection.

## Not verified

- **CUDA startup and sustained load.** The CUDA 12.8 profile and web UI were verified on the RTX 2080 Super Max-Q; results are in `README.md`. The first inference on the cup replay took 2.9 s and briefly triggered `degraded`, then recovered. That run predates the station markers, labels and voice: FPS and latency there are not measured with the station markers on (the default), and no tag has been taught there (see M4). Long-duration performance and the CUDA 13.0 build on this machine remain unverified.
- **Camera orientation on the real arm** (`camera.rotate_deg`, `flip_h`, `flip_v`). The check is in `README.md` under "Camera". If the mount needs exactly one of `flip_h` and `flip_v`, the picture is mirrored and the station markers are searched a second time, flipped back (`aruco.StationDetector`, `mirrored`). That second search has read markers only on synthetic frames, never through a real mirrored camera.
- **A camera mounted at 90° or 270°.** It needs `detector.imgsz: [640, 384]` and a new bake. That shape has never been run.
- **The class list** `[cup, bottle, pill bottle, box]`. It is a guess until the demo objects are known. On the sample clips "pill bottle" almost never wins and "box" does not match small blocks or trays.
- **Thresholds on our own footage.** They were set on public clips from other cameras. The one recording of ours, `recordings/run1.mp4`, never brought an object closer than "far", so `distance.thresholds` and the behaviour at grasp range are untested on our camera. `selection.coast_s` and `selection.conf_keep` are open; the numbers are in `README.md` under "What the sample clips showed".
- **Real consumers.** The `vision` stream has been read by `subscribe_print`, the tests and the web console (through the bridge, also as the launcher starts it). No run with the haptics or the team's speech output is recorded in this folder. On the command side, vision has taken commands from `send_command`, the web console through the bridge (a box drawn on its camera view included), and the voice process fed typed lines.

## One target at a time, the `labels` topic, Gemini's extra views: built, run in tests only

Built on 15 November 2026 (`README.md`: "One target at a time", "The `labels` topic", "More views from Gemini"). Not checked:

- **The lock on a live camera.** The selector's new rules (no rival takes over; a lost target is waited for `selection.reacquire_s` within `selection.reacquire_dist`) have run in `tests/test_selection.py` and `tests/test_pipeline_e2e.py` only. `reacquire_s` (1.5) and `reacquire_dist` (0.2) are guesses: the wait compares places in the picture, so a moving wrist camera may need a wider distance or a shorter wait. `scripts/eval_clips.py` was not rerun on the sample clips with them.
- **A wrong lock stays.** Vision no longer corrects itself when it locked the wrong object while the right one is in view; the operator has to choose the target again.
- **Gemini's descriptions end to end.** `../assist` sending `tag_text` with `prompts` to a running vision, vision adding the views, and the console showing the new view count have not run together. One real Gemini call gave usable descriptions; whether text views make YOLOE find a taught object from other sides or in other light is not measured (text tags as such were only checked in unit tests, see M4).
- **More views, slower detector.** A name now gets up to 3 more views, each one more class in the model. The frame rate with several enriched names was not measured.
- **The console's Target panel in a browser.** It replaced the Task panel and reads the `labels` topic; it has passed the frontend's type check and tests, and was not opened in a browser.
