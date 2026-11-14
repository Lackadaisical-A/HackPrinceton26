"""The `command` topic: what the console (and later voice) asks of vision.

Vision still only assists. A command narrows what it looks for and does nothing else; one that
cannot be honoured exactly is logged and ignored, never guessed at.
"""
from __future__ import annotations

import logging
from typing import Callable

import zmq

from .messages import TOPIC_COMMAND
from .transport import Subscriber

log = logging.getLogger(__name__)

SELECT_TARGET = "select_target"
CLEAR_TARGET = "clear_target"
SET_PHASE = "set_phase"


def apply_command(payload: object, classes: list[str], set_preferred_label: Callable[[str | None], None]) -> bool:
    """Act on one command payload. True if it changed what vision looks for."""
    kind = payload.get("type") if isinstance(payload, dict) else None
    if kind == SELECT_TARGET:
        label = payload.get("label")
        if not isinstance(label, str) or label not in classes:
            log.warning("command %s: %r is not one of detector.classes %s; ignored", SELECT_TARGET, label, classes)
            return False
        set_preferred_label(label)
        log.info("command: only '%s' can be the target now", label)
        return True
    if kind == CLEAR_TARGET:
        set_preferred_label(None)
        log.info("command: any object can be the target again")
        return True
    if kind == SET_PHASE:
        log.warning("command %s is not supported yet (milestone M2); ignored", SET_PHASE)
    else:
        log.debug("command of type %r is not for vision; ignored", kind)  # e.g. the console's set_haptics
    return False


class CommandReceiver:
    """Reads `command` messages. The main loop calls poll() on every pass, so it never waits.

    The publisher binds (the console bridge, on transport.command_sub) and this side connects, in
    either order: commands sent before both are up are lost, later ones arrive.
    """

    _MAX_PER_POLL = 16  # a flood of commands must not hold up a frame

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        try:
            self._sub = Subscriber(endpoint, topic=TOPIC_COMMAND)
        except zmq.ZMQError as exc:
            raise RuntimeError(f"could not connect the command subscriber to {endpoint}: {exc}") from exc

    def poll(self) -> list[dict]:
        """The commands that arrived since the last call."""
        payloads = []
        for _ in range(self._MAX_PER_POLL):
            try:
                item = self._sub.recv(0)
            except ValueError:  # not JSON, or not UTF-8
                log.warning("command: a message that could not be read was ignored")
                continue
            if item is None:
                break
            payloads.append(item[1])
        return payloads

    def close(self) -> None:
        self._sub.close()

    def __enter__(self) -> "CommandReceiver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
