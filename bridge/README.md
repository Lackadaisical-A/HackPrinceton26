# PAUL console bridge

Connects the web console (`../frontend`) to the rest of PAUL. The other programs talk over ZMQ, which a browser cannot speak, so this program relays:

- **Out:** every frame a ZMQ publisher sends (`<topic> <compact JSON>`) goes to every connected console as one WebSocket text message, unchanged. The bridge does not parse it.
- **Back:** `command` frames from a console are checked and published, unchanged, on a ZMQ PUB socket that the bridge binds. Vision listens there.

The camera picture does not pass through the bridge. Vision serves it directly as MJPEG at `http://127.0.0.1:8766/stream`.

## Run it

```powershell
uv sync                         # once
uv run python -m paul_bridge    # Ctrl+C stops it
```

It can be started before or after the publishers and the console. A publisher that is not running yet is not an error: its topic starts when it appears. The log says when a topic starts flowing, when one goes silent for 2 s, when a console connects or leaves, and each command it relays.

For live data in the console, run three programs, each in its own terminal:

```powershell
cd computer_vision; uv run python -m paul_vision --source wrist    # or --source 0 for a laptop's built-in camera
cd bridge;          uv run python -m paul_bridge
cd frontend;        npm run dev
```

Then open the console, go to Signals, choose "Live bridge" and connect.

## Addresses

| What | Default | Flag |
|---|---|---|
| Consoles connect here (WebSocket) | `127.0.0.1:8765` | `--listen HOST:PORT` |
| ZMQ publishers relayed to the consoles | `tcp://127.0.0.1:5555` (vision), `:5556` (pressure), `:5558` (robot) | `--from ENDPOINT`, repeat for several |
| ZMQ endpoint the bridge binds for commands | `tcp://127.0.0.1:5557` | `--commands ENDPOINT` |

The defaults match `computer_vision/config.yaml` (`transport`) and the console's default bridge address. Passing `--from` replaces the whole default list. `--verbose` also logs every dropped message.

**Only one program can bind port 5557.** The bridge is that program, so any other sender of commands (voice, later) has to send through the bridge or use another endpoint. This is not decided yet.

## Who may connect

- The bridge listens on this computer only (`127.0.0.1`).
- A web page may connect only if it is served from this computer (`localhost`, `127.0.0.1` or `[::1]`, any port). A page from any other site is refused with HTTP 403, because any web site a browser visits can try to open a WebSocket to this computer.
- `--origin http://192.168.1.20:5173` also accepts a console served from that origin. Repeat it for several.
- A client that sends no `Origin` header is not a browser (a script, a test). It is accepted: such a program could publish on ZMQ directly, so refusing it would protect nothing.
- `--listen 0.0.0.0:8765` opens the bridge to the network and logs a warning. Any program on the network can then read the data and send commands. Nothing is encrypted and there are no passwords.

## What a command must look like

One text message: `command`, a space, then a JSON object with a string `type`. For example:

```
command {"t":1731542400.2,"type":"select_target","label":"cup"}
```

Anything else (another topic, text that is not JSON, binary) is dropped and counted, and the first one from each console is logged. A message over 4096 bytes closes that console's connection; the console reconnects by itself. The bridge does not decide which command types exist: the receiver does (`computer_vision/paul_vision/commands.py`).

## Slow consoles

Each console has its own queue of 256 frames. A console that reads too slowly loses its oldest frames, not its newest, and does not hold up the others. For live data a late frame is worth less than the next one.

## Tests

```powershell
uv run pytest    # 47 tests, real ZMQ and real WebSockets on free local ports; nothing else needs to run
```

## What has been verified

On 14 November 2026, on the development laptop, with vision reading the laptop's built-in camera and the console in headless Chrome:

- `vision` frames reached the console and drove the overlay, the frame rate and the frame age readouts.
- Choosing "Cup" and then "Any object" in the console produced `select_target` and `clear_target` commands that the bridge relayed and vision acted on (both logged it).
- With vision stopped and started again while the bridge and the console stayed up, data resumed without restarting either.

## What has not

- Physical robot feedback and pressure: the pot controller now publishes `robot`, including
  labeled previews verified through this bridge alongside real vision. Physical servo feedback
  and the pressure publisher still need a hardware test. See [POT_TEST.md](../POT_TEST.md).
- More than one console at a time, outside the test suite.
- A console on another computer (`--listen` on a network address with `--origin`).
- Running for longer than a few minutes.
- Any browser other than Chrome.

## Layout

```
paul_bridge/
  __main__.py    command line
  relay.py       the relay: ZMQ reader thread, one writer task per console, the command check
tests/
  test_relay.py
```
