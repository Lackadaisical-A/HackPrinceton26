"""Print the messages a publisher sends:  uv run python -m paul_vision.subscribe_print

This is also the reference for teammates writing a subscriber. The wire format is one ZMQ frame,
`<topic> <json>`, and a consumer should treat silence, or a `t` older than half a second, as "no guidance".
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Sequence

from . import clock
from .config import DEFAULT_CONFIG_PATH, TransportConfig, load_config
from .messages import TOPIC_COMMAND, TOPIC_PRESSURE, TOPIC_ROBOT, TOPIC_VISION, validate_vision
from .transport import Subscriber

_POLL_S = 0.5    # short, so Ctrl+C is noticed promptly on Windows
_QUIET_S = 2.0   # say so when nothing has arrived for this long


def format_vision(msg: dict, now: float) -> str:
    age_ms = 1000 * (now - msg["t"])
    parts = [f"#{msg['frame_id']:<6} age {age_ms:4.0f} ms  {msg['status']:<14} {msg['phase']:<8}"]
    if msg.get("alert"):
        parts.append(f"ALERT {msg['alert'].upper()}")
    target, guidance = msg["target"], msg["guidance"]
    if target:
        parts.append(f"{target['label']} #{target['track_id']} {target['confidence']:.2f}")
    if guidance:
        parts.append(f"dx {guidance['dx']:+.2f} dy {guidance['dy']:+.2f}  {guidance['distance_hint']:<8}"
                     f"{' ALIGNED' if guidance['aligned'] else ''}")
    if msg["summary"]:
        parts.append(f'SAY "{msg["summary"]}"')
    return "  ".join(parts)


def quiet_notice(topic: str) -> str:
    """What a silence means depends on the topic: no `vision` is no guidance; no `command` is the normal case."""
    if topic == TOPIC_VISION:
        return (f"... no messages for {_QUIET_S:.0f} s. A consumer must treat this as 'no guidance'. "
                "Is `python -m paul_vision` running?")
    return f"... no '{topic}' messages for {_QUIET_S:.0f} s."


def default_endpoints(topic: str, transport: TransportConfig) -> list[str]:
    """Where `topic` is published, from the config: one endpoint, or every command sender's."""
    if topic == TOPIC_PRESSURE:
        return [transport.pressure_sub]
    if topic == TOPIC_ROBOT:
        return [transport.robot_sub]
    if topic == TOPIC_COMMAND:
        return list(transport.command_sub)
    return [transport.vision_pub]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.subscribe_print", description=__doc__.split("\n\n")[0])
    parser.add_argument("--endpoint", action="append",
                        help="connect here (repeatable). Default, by --topic: vision -> transport.vision_pub, "
                             "pressure -> transport.pressure_sub, robot -> transport.robot_sub, "
                             "command -> every transport.command_sub entry")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--topic", default=TOPIC_VISION)
    parser.add_argument("--conflate", action="store_true", help="keep only the newest message (what haptics should do)")
    parser.add_argument("--raw", action="store_true", help="print the JSON as received")
    parser.add_argument("--count", type=int, help="exit after this many messages")
    parser.add_argument("--timeout", type=float, help="exit after this many seconds")
    args = parser.parse_args(argv)

    endpoints = args.endpoint or default_endpoints(args.topic, load_config(args.config).transport)
    print(f"subscribed to '{args.topic}' on {', '.join(endpoints)}  (Ctrl+C to stop)", flush=True)
    received = invalid = 0
    statuses: dict[str, int] = {}
    started = last_seen = time.monotonic()
    announced_quiet = False
    try:
        with Subscriber(endpoints, args.topic, conflate=args.conflate) as sub:
            while args.count is None or received < args.count:
                if args.timeout is not None and time.monotonic() - started >= args.timeout:
                    break
                item = sub.recv(_POLL_S)
                if item is None:
                    if not announced_quiet and time.monotonic() - last_seen >= _QUIET_S:
                        print(quiet_notice(args.topic), flush=True)
                        announced_quiet = True
                    continue
                last_seen, announced_quiet = time.monotonic(), False
                received += 1
                topic, msg = item
                if args.raw:
                    print(f"{topic} {json.dumps(msg, separators=(',', ':'))}", flush=True)
                if topic == TOPIC_VISION:
                    try:
                        validate_vision(msg)
                    except ValueError as exc:
                        invalid += 1
                        print(f"INVALID MESSAGE ({exc}): {msg}", flush=True)
                        continue
                    statuses[msg["status"]] = statuses.get(msg["status"], 0) + 1
                    if not args.raw:
                        print(format_vision(msg, clock.now()), flush=True)
                elif not args.raw:
                    print(f"{topic}: {msg}", flush=True)
    except KeyboardInterrupt:
        pass
    elapsed = time.monotonic() - started
    print(f"{received} messages in {elapsed:.1f} s ({received / elapsed if elapsed else 0:.1f}/s), "
          f"{invalid} invalid, by status: {statuses or 'none'}", flush=True)
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
