"""The `command` topic: what the console, voice and send_command ask of vision.

Vision still only assists. A command narrows what it looks for, tells it which task phase the operator is in, or
teaches it a name (M4), and does nothing else; one that cannot be honoured exactly is logged and ignored, never
guessed at. A label command that is refused is also answered with a spoken sentence (label_manager).

Adding a command: give `CommandHooks` a field for the pipeline method it needs, and register a function with
`@_handles("<type>")` that checks the payload and calls it. `apply_command` dispatches by `type`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import zmq

from .messages import TOPIC_COMMAND, is_number
from .phase import PHASES
from .transport import Subscriber

log = logging.getLogger(__name__)

SELECT_TARGET = "select_target"
CLEAR_TARGET = "clear_target"
SET_PHASE = "set_phase"
TAG_VISUAL = "tag_visual"   # {"name": "my flask", "bbox": [x1, y1, x2, y2] (0..1) or null = the locked target}
TAG_TEXT = "tag_text"       # {"name": "my mug", "prompt": "red mug"}; prompt defaults to the name
UNTAG = "untag"             # {"name": "my flask"}
LABEL_COMMANDS = (TAG_VISUAL, TAG_TEXT, UNTAG)


@dataclass(frozen=True)
class LabelRequest:
    """A tag_visual, tag_text or untag command, read but not judged: the label manager decides."""
    kind: str                                              # one of LABEL_COMMANDS
    name: str                                              # normalized: lower case, single spaces
    bbox: tuple[float, float, float, float] | None = None  # tag_visual: normalized box; None = the locked target
    prompt: str | None = None                              # tag_text: what to look for (the name if not given)
    problem: str | None = None                             # set when the payload itself is unusable: a sentence


@dataclass(frozen=True)
class CommandHooks:
    """What commands may change in the running pipeline, one hook per kind of change."""
    # The labels select_target accepts: detector.classes, or a function giving them plus the live tag names.
    classes: Sequence[str] | Callable[[], Sequence[str]]
    set_preferred_label: Callable[[str | None], None]     # Pipeline.set_preferred_label
    set_phase: Callable[[str], None] | None = None        # Pipeline.set_phase; None where phases are not run
    teach: Callable[[LabelRequest], bool] | None = None   # LabelManager.teach; None where labels cannot change

    def labels(self) -> Sequence[str]:
        return self.classes() if callable(self.classes) else self.classes


_Handler = Callable[[dict, CommandHooks], bool]
_HANDLERS: dict[str, _Handler] = {}


def _handles(kind: str) -> Callable[[_Handler], _Handler]:
    def register(fn: _Handler) -> _Handler:
        _HANDLERS[kind] = fn
        return fn
    return register


@_handles(SELECT_TARGET)
def _select_target(payload: dict, hooks: CommandHooks) -> bool:
    label = payload.get("label")
    known = list(hooks.labels())
    if not isinstance(label, str) or label not in known:
        log.warning("command %s: %r is not one of detector.classes or the tag names %s; ignored",
                    SELECT_TARGET, label, known)
        return False
    hooks.set_preferred_label(label)
    log.info("command: only '%s' can be the target now", label)
    return True


@_handles(CLEAR_TARGET)
def _clear_target(payload: dict, hooks: CommandHooks) -> bool:
    hooks.set_preferred_label(None)
    log.info("command: any object can be the target again")
    return True


@_handles(SET_PHASE)
def _set_phase(payload: dict, hooks: CommandHooks) -> bool:
    phase = payload.get("phase")
    if not isinstance(phase, str) or phase not in PHASES:
        log.warning("command %s: %r is not one of %s; ignored", SET_PHASE, phase, ", ".join(PHASES))
        return False
    if hooks.set_phase is None:
        log.warning("command %s: phases are not run here; ignored", SET_PHASE)
        return False
    hooks.set_phase(phase)
    log.info("command: phase set to '%s'", phase)
    return True


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())  # the same rule as labels.normalize_name, without importing numpy here


def parse_label_request(payload: dict) -> LabelRequest:
    """Read a label command. Never raises: an unusable payload gives a request whose `problem` says why."""
    kind = payload.get("type")
    raw_name = payload.get("name")
    name = _normalize(raw_name) if isinstance(raw_name, str) else ""
    if kind == TAG_VISUAL:
        raw = payload.get("bbox")
        if raw is None:
            return LabelRequest(kind, name)
        if not (isinstance(raw, (list, tuple)) and len(raw) == 4 and all(is_number(c) for c in raw)):
            return LabelRequest(kind, name, problem=f"Cannot learn {name or 'that'}: the box is not valid.")
        return LabelRequest(kind, name, bbox=tuple(float(c) for c in raw))
    if kind == TAG_TEXT:
        raw = payload.get("prompt")
        if raw is None:
            return LabelRequest(kind, name, prompt=name)
        prompt = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not prompt:
            return LabelRequest(kind, name, problem=f"Cannot learn {name or 'that'}: the description is empty.")
        return LabelRequest(kind, name, prompt=prompt)
    if kind == UNTAG:
        return LabelRequest(kind, name)
    raise ValueError(f"{kind!r} is not a label command")


def _label_command(payload: dict, hooks: CommandHooks) -> bool:
    if hooks.teach is None:
        log.warning("command %s: labels cannot change here; ignored", payload.get("type"))
        return False
    return hooks.teach(parse_label_request(payload))


for _kind in LABEL_COMMANDS:
    _handles(_kind)(_label_command)


def apply_command(payload: object, hooks: CommandHooks) -> bool:
    """Act on one command payload. True if it changed something. Never raises: a payload from a socket must not
    stop vision, so an error while acting on one is logged with the command's type and the command is ignored."""
    kind = None
    try:
        kind = payload.get("type") if isinstance(payload, dict) else None
        handler = _HANDLERS.get(kind) if isinstance(kind, str) else None
        if handler is None:
            log.debug("command of type %r is not for vision; ignored", kind)  # e.g. the console's set_haptics
            return False
        return handler(payload, hooks)
    except Exception:
        log.exception("command of type %.40r could not be applied; ignored", kind)
        return False


class CommandReceiver:
    """Reads `command` messages from every sender. The main loop calls poll() on every pass, so it never waits.

    Each sender binds its own endpoint (transport.command_sub: the console bridge, voice, send_command) and this
    side connects to all of them on one socket, in either order: commands sent before both ends are up are lost,
    later ones arrive.
    """

    _MAX_PER_POLL = 16  # a flood of commands must not hold up a frame

    def __init__(self, endpoints: str | Sequence[str]):
        self.endpoints = [endpoints] if isinstance(endpoints, str) else list(endpoints)
        try:
            self._sub = Subscriber(self.endpoints, topic=TOPIC_COMMAND)
        except zmq.ZMQError as exc:
            raise RuntimeError(f"could not connect the command subscriber to {', '.join(self.endpoints)}: "
                               f"{exc}") from exc

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
