# PAUL assist (Gemini)

This process asks Gemini about the wrist camera's picture and shows the operator three things:

- what the camera sees
- **visible** signs about the patient, with an urgency of `none`, `watch` or `urgent`
- suggested actions, and up to 3 items the patient may need

Scene assist is advisory only. It never sends commands to vision or the arm (the one command assist does send by itself is described under "More views of a taught object"). In the console's **Assist** panel the operator clicks **Find** to make an item vision's target. If vision doesn't know the item yet, the button reads **Learn & find**: it teaches vision the name first with `tag_text` (about 30–50 s), then selects it.

Gemini is told not to diagnose. "Patient observations" are only what is visible in the picture.

## Smart replay

The same process is Gemini's side of **smart replay** (`../robot_control/WEARABLE.md`, "Smart replay"):

- While the operator records a motion, it keeps a timestamped picture from the camera every half second.
- When the recording finishes it sends Gemini the pictures at the recording's waypoints and publishes what Gemini understood on the `task` topic: a summary, the rule, and ordered steps with what each should achieve. The console shows this for the operator to confirm.
- During a smart replay the robot asks for one action per turn. Assist sends Gemini the brief, a few pictures from the demonstration, the camera now, the arm's state and the result of the last action, and publishes the chosen action on the `agent` topic.

It still never opens the servo bus. The robot process checks every action again, clamps it, and carries it out within its own limits; Stop, the joystick button and stale wearable input end a smart replay exactly as they end a plain one.

A quota or overload error while studying is retried every 15 s (up to 5 times) with the same pictures. During a run, four failed turns in a row hand over to the operator.

To see what Gemini makes of a set of pictures without the robot (one waypoint per picture, in name order):

```powershell
uv run --python 3.12 python -m paul_assist --once --frames path\to\pictures
```

## Setup

1. Put the key in the repo root `.env`. That file is gitignored.
   ```
   GEMINI_API_KEY=your-key
   ```
   Optionally set `GEMINI_MODEL=...` too. The default is `gemini-3.8-flash`.
2. Install the dependencies once:
   ```powershell
   cd assist
   uv sync --python 3.12
   ```

## Run

The team launcher (`..\demo.ps1`, `leslider_control/session.py`) starts assist next to the bridge and vision. Its log is `runs/pot-test/<time>/assist.log`.

To run it by hand:

```powershell
uv run python -m paul_assist                          # needs vision's stream on 18766; pass --camera-url for 8766
uv run python -m paul_assist --once --image room.jpg  # one analysis of a file, printed as JSON
uv run pytest
```

## Wiring

```
console "Analyse scene" --assist_request--> bridge PUB :5557 --> paul_assist
paul_assist: one picture from http://127.0.0.1:18766/stream + known names (vision config.yaml + labels/library.json)
          --> Gemini (structured JSON) --> `assist` on PUB :5563 --> bridge --> console
```

### More views of a taught object

Vision learns an object from one snapshot, so it knows it from one side in one light. For every taught name, assist shows Gemini that snapshot (with the object's box) and asks for short descriptions of the same object from other sides, from above, lying down, and in dim or harsh light. It sends them to vision as one command, which vision adds to the name as extra views:

```
vision labels/library.json + refs/<id>.png --> Gemini --> command {"type":"tag_text","name":"vaseline","prompts":[...]} on PUB :5564 --> vision
```

- It runs by itself for every name in vision's library that it has not dealt with, also names taught in earlier sessions. A name taught by description is described without a picture.
- It waits until vision is idle (the `busy` field of vision's `labels` topic) and only sends while vision listens.
- `<labels dir>/enriched.json` records what is done, so a name is described once. A failure is tried once more, then left.
- `--enrich-count` (default 3) sets how many descriptions; `--no-enrich` turns it off; `--enrich-commands` is the endpoint, which must be one of vision's `transport.command_sub`.
- The code is `paul_assist/enrich.py`; tests are in `tests/test_enrich.py`.

Not verified live: on 15 November 2026 one real Gemini call for a stored snapshot gave three usable descriptions in 7.1 s, and the rest ran in tests with a fake Gemini.

### The `assist` message

```json
assist {"t":1731542400.5,"seq":2,"status":"ok","detail":null,"model":"gemini-2.5-flash",
        "result":{"scene":"...","patient":{"present":true,"observations":["..."],"urgency":"watch"},
                  "suggestions":[{"text":"Hand the patient the bottle","item":"bottle"}],
                  "items":[{"name":"bottle","prompt":"clear water bottle","reason":"...","visible":true,"known":true}]}}
```

- `status` is one of:
  - `working`: an analysis has started.
  - `ok`: `result` is set.
  - `error`: `detail` says why, for example no key, no camera picture, a quota error or a timeout.
- The last message is repeated every 5 s, so a console that connects late still gets it. A new `seq` means a new message.
- Requests are ignored while an analysis is running, and for `--cooldown` seconds (3 s) after one starts.
- The console's **Auto** switch asks again every 20 s.

## Patient state and touch checks

`result.patient.state` is one of `awake`, `eyes_closed`, `distressed`, `unclear` or `not_visible`. One picture cannot tell sleep from unconsciousness, so `eyes_closed` covers both.

For `eyes_closed`, assist always does three things, whatever Gemini returns:
- adds a **wellness nudge** as the first touch check
- raises the urgency to at least `watch`
- has the console say *"Nudge the patient gently to check for wellness."*, if Speak is on

On the next analysis within 3 minutes, Gemini is told what it said last time. If it had suggested a nudge and the patient still looks the same, it is told to mark the case `urgent`, which raises the red alert and puts "Alert the caregiver now" first.

`result.touch_checks` has up to 3 gentle touches, `{kind, text, reason}`. They are light touches on the shoulder, arm or hand only, never the head, face, neck or chest, and never near wounds or lines.

| kind | when |
|---|---|
| `wellness_nudge` | the patient is still with eyes closed, slumped, or not reacting |
| `attention_tap` | the patient is awake but hasn't noticed the robot |
| `guided_handover` | handing over to a weak or unsteady grip: let go only when the pads feel the patient take it |
| `weight_check` | lift a cup or bottle to feel whether it is empty |
| `move_within_reach` | the patient is straining toward an item |
| `call_button` | press a visible nurse-call button for a patient who can't |
| `comfort_adjust` | a slipped blanket, or something about to fall |
