"""python -m paul_assist: Gemini for the console (scene suggestions) and for smart replay.

    uv run python -m paul_assist                          # beside the launcher's bridge and vision
    uv run python -m paul_assist --once --image room.jpg  # one scene analysis of a file, printed; no ZMQ
    uv run python -m paul_assist --once --frames demo/    # study a folder of pictures as a recording; no ZMQ

Scene suggestions (`assist`) are advisory: the operator acts on them. For smart replay it publishes `task`
(what Gemini understood from a recording) and `agent` (the one action Gemini chose for the turn the robot is
waiting on). It never opens the servo bus: the robot process validates each action and carries it out within
its own limits, and only after the operator starts a smart replay.

It sends vision one kind of command by itself: `tag_text` with `prompts`, Gemini's descriptions of an object the
operator taught, so vision knows that object from more than its one snapshot (enrich.py; off with --no-enrich).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .analysis import TOPIC
from .camera import FrameRing, grab_jpeg, read_jpeg_file
from .enrich import Enricher, read_library
from .gemini import DEFAULT_MODEL
from .known import known_labels
from .service import Assistant
from .smart import SmartAssist

log = logging.getLogger("paul_assist")

ROOT = Path(__file__).resolve().parents[2]
REQUEST_TYPE = "assist_request"
REPUBLISH_S = 5.0  # a console that connects late still gets the last answer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="paul_assist", description=__doc__.splitlines()[0])
    p.add_argument("--camera-url", default="http://127.0.0.1:18766/stream",
                   help="vision's MJPEG stream (the launcher's port is 18766; vision alone uses 8766)")
    p.add_argument("--commands", default="tcp://127.0.0.1:5557", help="the bridge's command PUB (connects)")
    p.add_argument("--vision", default="tcp://127.0.0.1:5555", help="vision's PUB, for context (connects)")
    p.add_argument("--calibration", default="tcp://127.0.0.1:5562",
                   help="the robot controller's status PUB, read for the replay state (connects)")
    p.add_argument("--publish", default="tcp://127.0.0.1:5563",
                   help="where `assist`, `task` and `agent` are published (binds)")
    p.add_argument("--vision-config", type=Path, default=ROOT / "computer_vision" / "config.yaml",
                   help="read for detector.classes and the taught names")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    p.add_argument("--cooldown", type=float, default=3.0, help="seconds between two analyses")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for Gemini")
    p.add_argument("--enrich-commands", default="tcp://127.0.0.1:5564",
                   help="where the extra descriptions of taught objects are sent to vision as `command` (binds; "
                        "must be one of vision's transport.command_sub)")
    p.add_argument("--enrich-count", type=int, default=3, help="descriptions Gemini adds to each taught object")
    p.add_argument("--no-enrich", action="store_true", help="do not add descriptions to taught objects")
    p.add_argument("--once", action="store_true", help="analyse one picture, print the message and exit")
    p.add_argument("--image", help="with --once: a JPEG or PNG file instead of the camera")
    p.add_argument("--frames", type=Path,
                   help="with --once: a folder of JPEG/PNG pictures in name order, studied as one recording")
    return p.parse_args(argv)


def make_analyzer(args: argparse.Namespace):
    """(analyzer, None) or (None, why it is unavailable)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    except ImportError:
        pass
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None, f"No GEMINI_API_KEY in the environment or in {args.env_file}."
    try:
        from .gemini import GeminiAnalyzer
        return GeminiAnalyzer(key, args.model, args.timeout), None
    except ImportError as exc:
        return None, f"google-genai is not installed ({exc}). Run uv sync in assist/."


def study_folder(args: argparse.Namespace) -> int:
    """Print the brief Gemini writes for a folder of pictures: one waypoint per picture, a second apart."""
    analyzer, problem = make_analyzer(args)
    paths = sorted(p for p in args.frames.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    frames = FrameRing(args.camera_url)
    for index, path in enumerate(paths):
        frames.add(float(index), read_jpeg_file(str(path)))
    smart = SmartAssist(analyzer, frames, lambda: b"", unavailable=problem or "")
    frames.start = frames.stop = lambda: None  # type: ignore[method-assign]
    messages = smart.update({"phase": "ready", "recorded_wall": 0.0, "events": [],
                             "waypoints": [float(i) for i in range(len(paths))]})
    messages += smart.wait(args.timeout * 4 + 10)
    final = messages[-1][1] if messages else {"status": "error", "detail": "Need at least two pictures."}
    print(json.dumps(final, indent=2))
    return 0 if final["status"] == "ok" else 1


def run_once(args: argparse.Namespace) -> int:
    if args.frames:
        return study_folder(args)
    analyzer, problem = make_analyzer(args)
    grab = (lambda: read_jpeg_file(args.image)) if args.image else (lambda: grab_jpeg(args.camera_url))
    assistant = Assistant(analyzer, grab, lambda: known_labels(args.vision_config),
                          cooldown_s=0, unavailable=problem or "")
    messages = assistant.request(None) + assistant.wait(args.timeout + 10)
    final = messages[-1]
    print(json.dumps(final, indent=2))
    return 0 if final["status"] == "ok" else 1


class CommandSender:
    """Publishes `command` messages to vision on an endpoint of its own. An XPUB with XPUB_VERBOSER reports every
    subscribe and unsubscribe, so a command is only sent while vision listens (nothing is queued for later)."""

    def __init__(self, ctx, endpoint: str) -> None:
        import zmq

        self._zmq = zmq
        self._subscribers = 0
        self._sock = ctx.socket(zmq.XPUB)
        self._sock.setsockopt(zmq.XPUB_VERBOSER, 1)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.bind(endpoint)
        self.endpoint = self._sock.getsockopt_string(zmq.LAST_ENDPOINT)  # the real one, for a port chosen by the OS

    def _drain(self) -> None:
        while self._sock.poll(0):
            frame = self._sock.recv()
            if frame[:1] in (b"\x00", b"\x01") and b"command ".startswith(frame[1:]):
                self._subscribers = max(0, self._subscribers + (1 if frame[:1] == b"\x01" else -1))

    def send(self, command: dict) -> bool:
        self._drain()
        if self._subscribers == 0:
            return False
        self._sock.send_string(f"command {json.dumps(command, separators=(',', ':'))}")
        return True


def run(args: argparse.Namespace) -> int:
    import zmq

    analyzer, problem = make_analyzer(args)
    if problem:
        log.warning("%s Requests will be answered with an error.", problem)
    else:
        log.info("Gemini model %s ready", args.model)
    assistant = Assistant(analyzer, lambda: grab_jpeg(args.camera_url), lambda: known_labels(args.vision_config),
                          cooldown_s=args.cooldown, unavailable=problem or "")
    smart = SmartAssist(analyzer, FrameRing(args.camera_url), lambda: grab_jpeg(args.camera_url),
                        unavailable=problem or "")

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.LINGER, 0)
    pub.bind(args.publish)
    commands = ctx.socket(zmq.SUB)
    commands.connect(args.commands)
    commands.setsockopt(zmq.SUBSCRIBE, b"command ")
    vision = ctx.socket(zmq.SUB)
    vision.setsockopt(zmq.CONFLATE, 1)
    vision.connect(args.vision)
    vision.setsockopt(zmq.SUBSCRIBE, b"vision ")
    poller = zmq.Poller()
    poller.register(commands, zmq.POLLIN)
    poller.register(vision, zmq.POLLIN)
    robot = ctx.socket(zmq.SUB)
    robot.setsockopt(zmq.CONFLATE, 1)
    robot.connect(args.calibration)
    robot.setsockopt(zmq.SUBSCRIBE, b"calibration ")
    poller.register(robot, zmq.POLLIN)
    log.info("smart replay: reading the robot's replay state on %s", args.calibration)
    labels = ctx.socket(zmq.SUB)
    labels.setsockopt(zmq.CONFLATE, 1)
    labels.connect(args.vision)
    labels.setsockopt(zmq.SUBSCRIBE, b"labels ")
    poller.register(labels, zmq.POLLIN)
    enricher = None
    if args.no_enrich or analyzer is None or args.enrich_count < 1:
        log.info("taught objects get no extra descriptions (%s)",
                 "--no-enrich" if args.no_enrich else problem or "--enrich-count is 0")
    else:
        try:
            sender = CommandSender(ctx, args.enrich_commands)
        except zmq.ZMQError as exc:
            log.warning("taught objects get no extra descriptions: cannot bind %s (%s)", args.enrich_commands, exc)
        else:
            enricher = Enricher(analyzer, lambda: read_library(args.vision_config), sender.send,
                                count=args.enrich_count)
            log.info("taught objects get %d extra descriptions from Gemini, sent to vision on %s",
                     args.enrich_count, args.enrich_commands)
    log.info("publishing `%s` on %s; listening for %s on %s", TOPIC, args.publish, REQUEST_TYPE, args.commands)

    latest_vision: dict | None = None
    last_sent = 0.0

    def publish(message: dict) -> None:
        nonlocal last_sent
        pub.send_string(f"{TOPIC} {json.dumps(message, separators=(',', ':'))}")
        last_sent = time.monotonic()

    try:
        while True:
            events = dict(poller.poll(100))
            if vision in events:
                try:
                    latest_vision = json.loads(vision.recv_string().partition(" ")[2])
                except ValueError:
                    pass
            if commands in events:
                frame = commands.recv_string()
                try:
                    command = json.loads(frame.partition(" ")[2])
                except ValueError:
                    command = None
                if isinstance(command, dict) and command.get("type") == REQUEST_TYPE:
                    for message in assistant.request(latest_vision):
                        publish(message)
            for message in assistant.poll():
                publish(message)
            if enricher is not None:
                if labels in events:
                    try:
                        seen = json.loads(labels.recv_string().partition(" ")[2])
                    except ValueError:
                        seen = None
                    if isinstance(seen, dict):
                        enricher.saw_labels(seen)
                enricher.poll()
            smart_messages = smart.poll()
            if robot in events:
                try:
                    status = json.loads(robot.recv_string().partition(" ")[2])
                except ValueError:
                    status = None
                if isinstance(status, dict):
                    smart_messages += smart.update(status.get("replay"), latest_vision)
            for topic, message in smart_messages:
                pub.send_string(f"{topic} {json.dumps(message, separators=(',', ':'))}")
            if assistant.last is not None and not assistant.busy and time.monotonic() - last_sent > REPUBLISH_S:
                publish(assistant.last)
    except KeyboardInterrupt:
        return 0
    finally:
        ctx.destroy(linger=0)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    return run_once(args) if args.once else run(args)


if __name__ == "__main__":
    sys.exit(main())
