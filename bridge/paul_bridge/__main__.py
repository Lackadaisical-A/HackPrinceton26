"""Run the console bridge:  uv run python -m paul_bridge

It relays every ZMQ topic to the web console at ws://127.0.0.1:8765 and the console's commands
back to ZMQ. Start it before or after the publishers; the order does not matter. Ctrl+C stops it.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Sequence

from .relay import (DEFAULT_COMMANDS, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SOURCES, Relay, RelayError,
                    is_loopback)

log = logging.getLogger("paul_bridge")


def parse_listen(text: str) -> tuple[str, int]:
    """'127.0.0.1:8765' or just '8765'."""
    host, colon, port = text.rpartition(":")
    try:
        number = int(port)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not HOST:PORT, for example 127.0.0.1:8765") from None
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError(f"port {number} is not in 0..65535")
    return (host.strip("[]") if colon and host else DEFAULT_HOST), number


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m paul_bridge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", type=parse_listen, default=(DEFAULT_HOST, DEFAULT_PORT), metavar="HOST:PORT",
                        help=f"where consoles connect (default {DEFAULT_HOST}:{DEFAULT_PORT})")
    parser.add_argument("--from", dest="sources", action="append", metavar="ENDPOINT",
                        help="a ZMQ publisher to relay; repeat for several "
                             f"(default: {' '.join(DEFAULT_SOURCES)})")
    parser.add_argument("--commands", default=DEFAULT_COMMANDS, metavar="ENDPOINT",
                        help=f"where the console's commands are published (default {DEFAULT_COMMANDS})")
    parser.add_argument("--origin", dest="origins", action="append", default=[], metavar="ORIGIN",
                        help="also accept a console served from this origin, for example http://192.168.1.20:5173; "
                             "repeat for several (pages served from this computer are always accepted)")
    parser.add_argument("--verbose", action="store_true", help="also log every dropped message")
    return parser.parse_args(argv)


async def serve_until_cancelled(args: argparse.Namespace) -> None:
    host, port = args.listen
    async with Relay(args.sources or DEFAULT_SOURCES, args.commands, host, port, args.origins) as relay:
        log.info("consoles connect to %s", relay.url)
        log.info("relaying from %s", ", ".join(relay.sources))
        log.info("commands are published on %s", relay.command_endpoint)
        if not is_loopback(host):
            log.warning("listening on %s: any program on the network can read the data and send commands", host)
        await asyncio.get_running_loop().create_future()  # until Ctrl+C


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)  # its own connection log repeats ours
    try:
        asyncio.run(serve_until_cancelled(args))
    except RelayError as exc:
        log.error("startup failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
