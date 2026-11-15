"""Send one command to the running vision process:  uv run python -m paul_vision.send_command set_phase carry

  set_phase <phase>       one of search, approach, grasp, carry, place, release
  select_target <label>   one of detector.classes or a tag name (a name with a space may be quoted or not)
  clear_target            any object can be the target again
  tag_visual <name> [--bbox x1,y1,x2,y2]
                          learn what is in the box (0..1 of the picture) as <name>; without --bbox, the locked target
  tag_text <name> [--prompt "red mug"]
                          learn to look for the description (default: the name) as <name>
  untag <name>            forget <name>

Vision answers a tag command by speaking (the `summary` of the vision message): "Learning my flask.", then
"My flask is ready." once the model that knows the new name is serving frames (whether or not my flask is in
view), or why not.

It binds an endpoint that vision connects to (default: the last entry of transport.command_sub in config.yaml),
waits until vision has subscribed, sends the command once and exits. Only one process can bind a port, so it
cannot use the console bridge's or voice's endpoint while those run; pick another with --endpoint, listed in
transport.command_sub so that vision connects to it.

Exit status: 0 sent, 1 not sent (port taken, or nobody subscribed within --wait), 2 bad arguments or config.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Sequence

import zmq

from . import clock
from .commands import CLEAR_TARGET, SELECT_TARGET, SET_PHASE, TAG_TEXT, TAG_VISUAL, UNTAG
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .labels import library_dir, read_tag_names
from .messages import TOPIC_COMMAND, encode
from .phase import PHASES

# A SUB that connected before this process bound the port retries every 100 ms (ZMQ's default reconnect interval),
# so every subscriber has subscribed this long after the first one has.
_SETTLE_S = 0.25
_LINGER_MS = 1000  # close() waits at most this long to hand the command over
_DEFAULT_WAIT_S = 3.0


class EndpointBusy(Exception):
    """Another process holds the port."""


COMMANDS = (SET_PHASE, SELECT_TARGET, CLEAR_TARGET, TAG_VISUAL, TAG_TEXT, UNTAG)


def parse_bbox(text: str) -> list[float]:
    """`x1,y1,x2,y2`, each 0..1 of the picture, x1 < x2 and y1 < y2. Raises ValueError."""
    try:
        box = [float(part) for part in text.split(",")]
    except ValueError:
        box = []
    if len(box) != 4 or not (0.0 <= box[0] < box[2] <= 1.0 and 0.0 <= box[1] < box[3] <= 1.0):
        raise ValueError(f"--bbox needs x1,y1,x2,y2 with each value 0..1, x1 < x2 and y1 < y2 (got {text!r})")
    return box


def build_command(kind: str, value: str | None, classes: Sequence[str], *, bbox: str | None = None,
                  prompt: str | None = None) -> dict:
    """The payload for one command, checked as vision will check it. Raises ValueError saying what is wrong.
    `classes`: the labels select_target accepts (detector.classes and the tag names). Vision judges tag names
    itself and answers by voice."""
    if bbox is not None and kind != TAG_VISUAL:
        raise ValueError("--bbox is only for tag_visual")
    if prompt is not None and kind != TAG_TEXT:
        raise ValueError("--prompt is only for tag_text")
    if kind in (TAG_VISUAL, TAG_TEXT, UNTAG):
        if not value or not value.strip():
            raise ValueError(f"{kind} needs a name")
        payload = {"t": clock.now(), "type": kind, "name": value}
        if kind == TAG_VISUAL:
            payload["bbox"] = None if bbox is None else parse_bbox(bbox)
        elif kind == TAG_TEXT and prompt is not None:
            if not prompt.strip():
                raise ValueError("--prompt must not be empty")
            payload["prompt"] = prompt
        return payload
    if kind == SET_PHASE:
        if value not in PHASES:
            raise ValueError(f"set_phase needs one of: {', '.join(PHASES)} (got {value!r})")
        return {"t": clock.now(), "type": SET_PHASE, "phase": value}
    if kind == SELECT_TARGET:
        if value not in classes:
            raise ValueError(f"select_target needs one of detector.classes or a tag name: {', '.join(classes)} "
                             f"(got {value!r})")
        return {"t": clock.now(), "type": SELECT_TARGET, "label": value}
    if kind == CLEAR_TARGET:
        if value is not None:
            raise ValueError(f"clear_target takes no argument (got {value!r})")
        return {"t": clock.now(), "type": CLEAR_TARGET}
    raise ValueError(f"unknown command {kind!r}; use one of {', '.join(COMMANDS)}")


def _wait_for_subscribers(sock: zmq.Socket, wait_s: float) -> int:
    """How many subscribers to `command` arrived: waits up to wait_s for the first, then _SETTLE_S for others."""
    wanted = f"{TOPIC_COMMAND} ".encode("utf-8")
    count = 0
    deadline = time.monotonic() + wait_s
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return count
        if not sock.poll(max(1, int(left * 1000))):
            continue
        frame = sock.recv()
        # An XPUB receives each subscription as a byte 1 (0 = unsubscribe) followed by the topic prefix.
        if frame[:1] == b"\x01" and wanted.startswith(frame[1:]):
            count += 1
            if count == 1:
                deadline = time.monotonic() + _SETTLE_S


def send(endpoint: str, payload: dict, wait_s: float) -> int:
    """Bind `endpoint`, wait for subscribers, send `payload` once. Returns how many subscribers it went to (0 =
    not sent). Raises EndpointBusy if another process holds the port, zmq.ZMQError for other bind failures.
    """
    ctx = zmq.Context()
    sock = ctx.socket(zmq.XPUB)
    try:
        # Without VERBOSE an XPUB reports only the first subscription to a topic, so others could not be counted.
        sock.set(zmq.XPUB_VERBOSE, 1)
        sock.set(zmq.LINGER, _LINGER_MS)
        try:
            sock.bind(endpoint)
        except zmq.ZMQError as exc:
            if exc.errno == zmq.EADDRINUSE:
                raise EndpointBusy(endpoint) from None
            raise
        subscribers = _wait_for_subscribers(sock, wait_s)
        if subscribers:
            sock.send(encode(TOPIC_COMMAND, payload))
        return subscribers
    finally:
        sock.close()  # waits up to LINGER for the command to be handed over
        ctx.term()


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.send_command", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("value", nargs="*", help="the phase, the label, or the tag name")
    parser.add_argument("--bbox", help="tag_visual: x1,y1,x2,y2 in 0..1 of the picture (default: the locked target)")
    parser.add_argument("--prompt", help="tag_text: what to look for (default: the name)")
    parser.add_argument("--endpoint", help="bind here (default: the last entry of transport.command_sub)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    parser.add_argument("--wait", type=float, default=_DEFAULT_WAIT_S,
                        help=f"seconds to wait for vision to subscribe (default {_DEFAULT_WAIT_S:g})")
    return parser.parse_args(argv)


def _say(text: str) -> None:
    print(f"send_command: {text}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        _say(f"configuration: {exc}")
        return 2
    value = " ".join(args.value) if args.value else None
    labels = list(cfg.detector.classes)
    try:
        labels += [name for name in read_tag_names(library_dir(cfg)) if name not in labels]
    except ValueError as exc:
        _say(f"note: the tag names are unknown here ({exc})")
    try:
        payload = build_command(args.command, value, labels, bbox=args.bbox, prompt=args.prompt)
    except ValueError as exc:
        _say(str(exc))
        return 2
    if not args.wait > 0:
        _say("--wait must be above 0")
        return 2
    endpoint = args.endpoint or cfg.transport.command_sub[-1]
    if endpoint not in cfg.transport.command_sub:
        _say(f"note: vision only connects to transport.command_sub ({', '.join(cfg.transport.command_sub)}); "
             f"{endpoint} is not one of them")
    try:
        subscribers = send(endpoint, payload, args.wait)
    except EndpointBusy:
        _say(f"{endpoint} is in use by another process (only one process can bind a port); choose another with "
             "--endpoint, listed in transport.command_sub")
        return 1
    except zmq.ZMQError as exc:
        _say(f"cannot bind {endpoint}: {exc}; choose another with --endpoint")
        return 1
    if not subscribers:
        _say(f"nobody subscribed on {endpoint} within {args.wait:g} s; nothing was sent. "
             "Is `python -m paul_vision` running with this endpoint in transport.command_sub?")
        return 1
    print(f"sent {json.dumps(payload, separators=(',', ':'))} to {subscribers} subscriber"
          f"{'' if subscribers == 1 else 's'} on {endpoint}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
