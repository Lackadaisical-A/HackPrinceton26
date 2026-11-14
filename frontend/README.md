# PAUL console (frontend)

The operator's web app for PAUL: one screen that shows what the wrist camera sees, what the
gripper pads feel, what the armband is told to play, and where the arm is, plus the tools to run
and compare trials with and without haptic feedback.

The app starts on a built-in simulator and labels everything simulated as simulated. For live
data it connects to the bridge in `../bridge`, and shows the camera that the vision module is
watching (see "Live data" below).

## Run it

Built and tested with Node 22 (pinned in `.nvmrc`; 22.16 and 22.23 both pass).

```sh
cd frontend
npm install
npm run dev        # development server, http://localhost:5173
```

| Command             | What it does                                         |
| ------------------- | ---------------------------------------------------- |
| `npm run dev`       | Development server with hot reload                   |
| `npm run build`     | Type-check, then build the production site to `dist` |
| `npm run preview`   | Serve the built site locally                         |
| `npm test`          | Unit tests (Vitest)                                  |
| `npm run lint`      | Lint (oxlint)                                        |
| `npm run typecheck` | Type-check only                                      |

The build uses relative asset paths and hash routes (`#/trials`), so `dist` can be served from any
folder by any static file server with no rewrite rules.

More scripts: `npm run check` (typecheck, lint and tests in one go), `npm run test:watch`,
`npm run dev:lan` (dev server reachable from other devices on the network, e.g. to try the design
on the Windows laptop while editing on a Mac), and `npm run demo` (production build, served at
http://localhost:4173 and opened in the browser).

### Installing safely

- Use `npm ci`, not `npm install`. It installs exactly the versions in `package-lock.json`, which
  were audited (`npm audit`, `npm audit signatures`: 0 vulnerabilities, all signatures valid) on
  14 November 2026.
- `.npmrc` sets `ignore-scripts=true`, so no package's install script ever runs. That is how most
  npm malware gets onto a machine. The app needs none.
- Adding a package: check the exact name on npmjs.com first (lookalikes of Vite and Tailwind
  plugins were published as malware in 2026), prefer the official scopes (`@vitejs/*`,
  `@tailwindcss/*`), and avoid versions published in the last few days.

### Demo on the Windows laptop

1. Install Node 22 LTS from https://nodejs.org (the Windows installer; the defaults are fine).
2. Clone the repo, then double-click `demo.bat` in the repo root. It installs dependencies the
   first time, builds, and opens http://localhost:4173.

Serve the app from the laptop itself, not from the hosted copy, during a live demo: the bridge
(`ws://127.0.0.1:8765`) and the camera stream (`http://127.0.0.1:8766`) run on the same laptop, and
a page on `localhost` reaches them without the browser's local-network permission prompts.

`.gitattributes` keeps every checkout on LF line endings, so the Windows clone is byte-identical to
the macOS one.

### Continuous integration and sharing

`.github/workflows/frontend.yml` runs typecheck, lint, tests and the build on **macOS and Windows**
for every push and pull request that touches `frontend/`. It does not deploy anything.

To share a public copy (it runs on the simulator), run `npm run build` and drag the `dist` folder
onto https://app.netlify.com/drop. Repeat to update it.

## The three screens

**Console** (`#/`) is the working screen.

- Wrist camera: the one dark window on the page. The guidance overlay is drawn from vision
  messages: open brackets while steering, a closed box once aligned. When vision data is stale,
  silent or degraded, guidance is hidden and the window says why.
- Touch link: the four gripper pads and the four armband motors, the wire between them (drawn open
  when haptics are off), and 12-second traces of grip and haptic output. The haptics switch lives
  here and is locked while a trial runs.
- Trial bar: start and finish a trial, mark drops and deformations.
- Arm, Armband, Task and Event log panels.

**Trials** (`#/trials`) compares finished trials with haptics against trials without: duration,
drops, deformations and peak grip, as charts and as a table. Aborted trials are listed but not
charted.

**Signals** (`#/signals`) is the plumbing: choose the data source, see each stream's rate and
latest message, send commands, read the message formats, and run the simulator's fault tests
(squeeze now, stall vision).

### Keyboard

| Key           | Action                                         |
| ------------- | ---------------------------------------------- |
| `T`           | Start a trial, or finish the one that is running |
| `D`           | Mark a drop during a trial                     |
| `F`           | Mark a deformation during a trial              |
| `H`           | Haptics on or off (not during a trial)         |
| `R`           | Start or stop recording                        |
| `S`           | Read captions aloud, on or off                 |
| `1` `2` `3`   | Console, Trials, Signals                       |
| `?`           | Show the shortcut list                         |

Shortcuts are ignored while typing in a field or while a dialog is open.

## Data sources

**Simulator (default).** A scripted 36-second pick-and-place cycle that publishes every topic at
its nominal rate. Every other cycle includes an over-squeeze, so the grip caution can be seen
without touching anything. The camera window shows a drawn scene marked "Simulated scene".

**Live bridge.** A WebSocket, by default `ws://127.0.0.1:8765`, set on the Signals page. The app
reconnects with a backoff of 1, 2, 4, then 8 seconds. It never switches to simulated data on its
own.

### Live data

Live data needs two more programs on this computer, each in its own terminal. They can be started
in any order, before or after the app connects.

```sh
cd computer_vision && uv run python -m paul_vision --source wrist   # vision; --source 0 for a laptop's built-in camera
cd bridge && uv run python -m paul_bridge                           # the bridge
```

Then open Signals, choose "Live bridge" and connect. The choice is remembered.

- **The bridge** (`../bridge`) relays every ZMQ frame to the app as one WebSocket text message,
  unchanged, and relays the app's `command` frames back to ZMQ.
- **The camera picture** comes from the vision module, which serves the camera it is watching as
  MJPEG at `http://127.0.0.1:8766/stream`. That is the default stream address; it can be changed or
  cleared on the Signals page. The app does not open the camera itself, because only one program
  can hold a camera at a time and vision needs it. Without a stream the overlay is drawn on a
  blank window.
- **The picture reconnects by itself.** If vision is not running yet, or stops and starts again,
  the app asks for the stream again after 1, 2, then every 4 seconds, and at once when vision data
  resumes (`src/routes/console/cameraStream.ts`).
- **The overlay and the picture travel separately** (WebSocket and MJPEG), so on a moving object
  the box and the picture can be slightly out of step. By how much has not been measured.

### Wire format

Every message is one text frame: the topic, a space, then compact JSON. This is the same encoding
the Python side uses over ZMQ (`computer_vision/paul_vision/messages.py`).

```
vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{...},"guidance":{...},"summary":"Cup near, slightly left."}
pressure {"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}
command {"t":1731542400.2,"type":"select_target","label":"pill bottle"}
```

| Topic      | Standing     | Rate  | Source                                 |
| ---------- | ------------ | ----- | -------------------------------------- |
| `vision`   | contract     | 30 Hz | `computer_vision`, ZMQ port 5555       |
| `pressure` | contract     | 50 Hz | pressure bridge, ZMQ port 5556         |
| `imu`      | **proposed** | 50 Hz | BNO085 over serial                     |
| `robot`    | **proposed** | 30 Hz | arm controller, ZMQ port 5558          |
| `haptics`  | **proposed** | 50 Hz | haptics layer (DRV2605L drivers)       |
| `command`  | mixed        | –     | sent by this app                       |

"Contract" topics are defined by an existing module. "Proposed" topics were defined by this
frontend so the screens had something to show. Nothing publishes them yet, and their shapes are
open to change. Of the commands, `select_target`, `clear_target` and `set_phase` are contract;
`set_haptics` is proposed.

The types, validators and example frames are in `src/telemetry/contracts.ts`, and the Signals page
shows the same information in the app. Frames that fail validation are counted and dropped, never
displayed.

Vision messages follow the vision module's consumer rules: guidance is used only when
`status` is `"ok"`, and a message whose `t` is more than 0.5 s old, or no message at all, means no
guidance.

## How it is built

React 19, TypeScript, Vite 8, Tailwind CSS 4, zustand, React Router (hash routes). Fonts are
Atkinson Hyperlegible Next and Mono, bundled, with no network requests at run time.

The design rule that shapes the code: **high-rate data never passes through React.**

- `src/telemetry/bus.ts` keeps the latest message per topic and fixed-size `Float64Array` ring
  buffers for the traces. Publishing a message does not cause a React render.
- `src/render/scheduler.ts` runs one `requestAnimationFrame` loop for every canvas and live number
  on the page. It pauses when the tab is hidden, skips anything scrolled out of view, and caps the
  canvas pixel ratio at 2.
- `src/app/derive.ts` listens to the bus, and runs a timer to notice silence, and writes only
  discrete changes (phase, vision state, alerts) to the zustand session store, which is what React
  renders from.
- Trials and Signals are separate chunks, loaded when first opened.

```
src/
  app/          shell, top bar, shortcuts, runtime actions (connect, record, trials)
  config.ts     joints, pads, motors, limits, defaults: the numbers to check against hardware
  lib/          formatting and maths helpers
  render/       shared frame scheduler, canvas hook, colour tokens for canvas drawing
  routes/
    console/    camera, touch link, trial bar, arm, armband, task, event log
    trials/     comparison charts, table, statistics
    signals/    source, streams, latest messages, commands, formats, simulator tests
  state/        session (not saved), settings and trials (saved in the browser)
  styles/       design tokens and the few global rules
  telemetry/    contracts, bus, ring buffers, alerts, recorder, sources (simulator, WebSocket)
  ui/           plates, buttons, switches, chips, readouts
```

## What this app claims, and what it does not

- Grip is a **relative level from 0 to 1**, not a calibrated force. The app says so on screen.
- Haptic output is what the motors are **commanded** to play, not what the wearer feels.
- Simulated and sample data are always labelled, drawn with hatching, and never mixed into the
  comparison of live trials.
- There is no emergency stop in this app, and it must not be treated as one. It observes the arm
  and sends a few task commands; stopping the arm is the hardware's job.
- PAUL is a hackathon prototype, not a certified assistive or medical device.

## Assumptions to check against the hardware

These were chosen so the screens could be built. Most live in `src/config.ts`.

| Assumption | Where |
| --- | --- |
| Pad order: left tip, left base, right tip, right base | `PADS` |
| Motor order and positions: up, right, down, left around the forearm | `MOTORS` |
| Joint order and display ranges for the six SO-101 servos | `JOINTS` |
| The arm drawing is a schematic, not kinematics. Link lengths are illustrative and the elbow reads zero at 60° | `routes/console/armSchematic.ts` |
| Caution thresholds: grip 0.9, servo load 0.85, vision below 15 fps, frame age above 150 ms | `LIMITS` |
| Detector labels offered as targets: cup, bottle, pill bottle, box | `DETECTOR_CLASSES` |
| The shapes of `imu`, `robot`, `haptics` and `set_haptics` | `telemetry/contracts.ts` |
| Frame age compares the message's `t` with this computer's clock, so both clocks must agree | `routes/console/CameraView.tsx` |

## Recording, storage and motion

- **Recording** saves telemetry messages as JSON Lines (`paul-session-….jsonl`, or
  `paul-simulated-….jsonl` on the simulator), downloaded when recording stops. It does not record
  video. A recording stops itself at 1,000,000 messages.
- **Settings and trials** are kept in this browser's local storage under `paul.settings` and
  `paul.trials`. Nothing is sent anywhere. Clearing site data removes them.
- **Reduced motion.** With the system setting on, interface transitions are cut and the camera
  overlay snaps instead of easing. Data is still drawn at full rate, because a trace that lags the
  sensor would be a worse instrument.

## What has been verified

- `npm run typecheck`, `npm run lint`, `npm test` (246 tests in 12 files) and `npm run build` all
  pass.
- Every screen was checked in headless Chrome at 1440 px and 390 px wide against the simulator,
  including the over-squeeze caution, stalled vision, a running trial, haptics off, and the
  disconnected state. The production build was smoke-tested the same way.
- Live, on 14 November 2026, in headless Chrome on the development laptop, with vision reading the
  laptop's built-in camera and the bridge running:
  - The console showed the camera picture with the overlay, the vision rate and the frame age.
  - Choosing a target in the Task panel sent `select_target` and `clear_target`, and vision acted
    on both.
  - With vision stopped, the picture was hidden and the window said "No vision data". After vision
    started again the picture and the data came back without reloading the page.
  - With the console opened before vision, the window said the stream did not load, kept trying,
    and showed the picture once vision was up.
  - Settings saved by the earlier version of the app, which had no stream address, picked up the
    default address.

## What has not

- **No arm, no pads, no armband.** Only `vision` has come from a live publisher. `pressure`, `imu`,
  `robot` and `haptics` have only ever come from the simulator.
- **The wrist camera.** It was not plugged in during the live check, so the picture was the
  laptop's built-in camera. That camera delivered between 10 and 30 frames a second from one run
  to the next; at 10 the console shows "Vision rate low", which is correct.
- The live check was automated. Nobody has watched the live picture in an ordinary browser window
  for longer than a few minutes, or judged how well the overlay stays on a moving object.
- Only Chrome was used. Firefox, Safari and real phones are untested.
- No screen reader pass was done. The markup is written for one (labels, live regions, table
  semantics, text alternatives for the canvases), but it has not been listened to.
- Spoken captions use the browser's speech synthesis and were not tested by ear.
