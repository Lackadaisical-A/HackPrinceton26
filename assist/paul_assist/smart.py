"""Smart replay's Gemini side: study a recording once, then choose one action per turn while the robot asks.

Driven by the robot's `replay` status (from the `calibration` topic). Like service.py, this class only
returns the messages to publish, so it is testable without ZMQ, a camera or an API key.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Protocol

from . import agent, task
from .service import _short_reason

log = logging.getLogger(__name__)

Message = tuple[str, dict[str, Any]]
REFERENCE_PICTURES = 6      # demonstration pictures sent with every turn
REPUBLISH_ACTION_S = 0.5    # PUB/SUB can drop a message: repeat the answer until the robot takes it
REPUBLISH_TASK_S = 5.0
MAX_TURN_ERRORS = 4         # then hand over to the operator instead of letting the robot time out
STUDY_RETRY_S = 15.0        # a quota or overload error while studying a recording is retried, the pictures are kept
MAX_STUDY_TRIES = 5


class Generator(Protocol):
    model: str

    def generate(self, parts: list[Any], system: str, schema: dict[str, Any]) -> Any: ...


class Frames(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def clear(self) -> None: ...
    def nearest(self, wall: float, tolerance_s: float = 1.5) -> bytes | None: ...
    def latest(self, max_age_s: float = 1.5) -> bytes | None: ...


class SmartAssist:
    def __init__(self, analyzer: Generator | None, frames: Frames, grab: Callable[[], bytes], *,
                 unavailable: str = "Gemini is not configured.", retry_s: float = 3.0,
                 now: Callable[[], float] = time.time) -> None:
        self._analyzer, self._frames, self._grab = analyzer, frames, grab
        self._unavailable, self._retry_s, self._now = unavailable, retry_s, now
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._finished: list[Message] = []
        self._task_seq = 0
        self._phase: str | None = None
        # The recording that was studied, by its wall-clock start.
        self._studied: float | None = None
        self._study_tries: tuple[float | None, int] = (None, 0)
        self._study_retry_at = 0.0
        self._brief: dict[str, Any] | None = None
        self._points: list[float] = []
        self._events: list[dict[str, Any]] = []
        self._keyframes: dict[int, bytes] = {}
        self.task: dict[str, Any] | None = None
        self._task_sent = 0.0
        # The run being driven.
        self._run: Any = None
        self._history: list[str] = []
        self._last: tuple[int, dict[str, Any]] | None = None   # (seq, action) awaiting its result
        self._previous_picture: bytes | None = None
        self._answer: dict[str, Any] | None = None
        self._answer_key: tuple[Any, int] | None = None
        self._answer_sent = 0.0
        self._retry_at = 0.0
        self._errors = 0

    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def model(self) -> str | None:
        return self._analyzer.model if self._analyzer else None

    # --- Driven by the robot's status -------------------------------------------------------

    def update(self, replay: dict[str, Any] | None, vision: dict[str, Any] | None = None) -> list[Message]:
        """Take the robot's latest `replay` status. Returns what to publish now."""
        out: list[Message] = []
        phase = replay.get("phase") if isinstance(replay, dict) else None
        if phase == "recording" and self._phase != "recording":
            self._frames.clear()
        if phase in ("recording", "smart"):
            self._frames.start()
        else:
            self._frames.stop()
        self._phase = phase
        if not isinstance(replay, dict):
            return out
        now = self._now()
        if phase == "ready":
            out += self._maybe_study(replay)
        elif phase == "smart":
            out += self._maybe_turn(replay, vision, now)
        if self.task is not None and not self.busy and now - self._task_sent >= REPUBLISH_TASK_S:
            out.append((task.TOPIC, self.task))
        for topic, message in out:
            if topic == task.TOPIC:
                self.task, self._task_sent = message, now
        return out

    def _task_message(self, status: str, recording: float | None, **kwargs: Any) -> Message:
        self._task_seq += 1
        return task.TOPIC, task.build_task_message(self._task_seq, self._now(), status, recording=recording,
                                                   model=self.model, **kwargs)

    def _maybe_study(self, replay: dict[str, Any]) -> list[Message]:
        wall, points = replay.get("recorded_wall"), replay.get("waypoints")
        if not isinstance(wall, (int, float)) or not isinstance(points, list) or len(points) < 2:
            return []
        if wall == self._studied or self.busy or self._now() < self._study_retry_at:
            return []
        tries = self._study_tries[1] if self._study_tries[0] == wall else 0
        if tries >= MAX_STUDY_TRIES:
            return []
        self._study_tries = (wall, tries + 1)
        self._studied, self._brief = wall, None
        self._points = [float(p) for p in points]
        self._events = [e for e in replay.get("events") or [] if isinstance(e, dict) and "t" in e and "kind" in e]
        if self._analyzer is None:
            return [self._task_message("error", wall, detail=self._unavailable)]
        self._keyframes = {}
        for index in task.pick_keyframes(self._points, self._events):
            picture = self._frames.nearest(wall + self._points[index])
            if picture is not None:
                self._keyframes[index] = picture
        if len(self._keyframes) < 2:
            return [self._task_message("error", wall, detail="No camera pictures were captured during this "
                                       "recording. Record again with vision and assist running.")]
        self._start(self._study, wall)
        return [self._task_message("working", wall, detail="Gemini is studying the recording.")]

    def _maybe_turn(self, replay: dict[str, Any], vision: dict[str, Any] | None, now: float) -> list[Message]:
        state = replay.get("agent")
        if not isinstance(state, dict) or state.get("state") != "waiting" or type(state.get("seq")) is not int:
            return []
        run, seq = state.get("run"), state["seq"]
        if run != self._run:
            self._run, self._history, self._last, self._previous_picture = run, [], None, None
            self._answer = self._answer_key = None
            self._errors, self._retry_at = 0, 0.0
        key = (run, seq)
        if key == self._answer_key:
            if self._answer is not None and now - self._answer_sent >= REPUBLISH_ACTION_S:
                self._answer_sent = now
                return [(agent.TOPIC, self._answer)]
            return []
        if self.busy or now < self._retry_at:
            return []
        result = state.get("result") if isinstance(state.get("result"), dict) else None
        if self._last is not None and result is not None and result.get("seq") == self._last[0]:
            self._history.append(agent.history_entry(self._last[1], result))
            self._last = None
        if self._analyzer is None or self._brief is None or replay.get("recorded_wall") != self._studied:
            reason = (self._unavailable if self._analyzer is None else
                      "Gemini has not studied this recording. Record again with assist running, then confirm the task.")
            return [self._answer_with(key, {"action": "done", "why": reason, "text": reason}, now)]
        if self._errors >= MAX_TURN_ERRORS:
            self._errors = 0
            reason = "Gemini could not be reached several times in a row."
            return [self._answer_with(key, {"action": "ask_operator", "why": reason, "text": reason}, now)]
        arm = state.get("arm") if isinstance(state.get("arm"), dict) else None
        self._start(self._turn, key, arm, result, vision)
        return [(agent.TOPIC, agent.build_agent_message(now, run=run, seq=seq, status="working",
                                                        detail="Gemini is choosing the next action.", model=self.model))]

    def _answer_with(self, key: tuple[Any, int], action: dict[str, Any], now: float) -> Message:
        self._answer = agent.build_agent_message(now, run=key[0], seq=key[1], status="ok", action=action, model=self.model)
        self._answer_key, self._answer_sent = key, now
        self._last = (key[1], action)
        return agent.TOPIC, self._answer

    # --- Worker thread ----------------------------------------------------------------------

    def _start(self, target: Callable[..., None], *args: Any) -> None:
        self._thread = threading.Thread(target=target, args=args, name="assist-smart", daemon=True)
        self._thread.start()

    def _finish(self, *messages: Message, state: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._finished.append(("", state or {}))
            self._finished += messages

    def _study(self, wall: float) -> None:
        assert self._analyzer is not None
        started = time.monotonic()
        marks = task.waypoint_marks(self._points, self._events)
        parts: list[Any] = []
        for index, picture in sorted(self._keyframes.items()):
            parts += [f"Waypoint {index}" + (f" ({marks[index]})" if index in marks else "") + ":", picture]
        parts.append(task.build_brief_prompt(self._points, self._events))
        try:
            brief = task.normalize_brief(self._analyzer.generate(parts, task.BRIEF_SYSTEM, task.BRIEF_SCHEMA),
                                         len(self._points))
            log.info("studied the recording in %.1f s: %s", time.monotonic() - started, brief["summary"])
            self._finish(self._task_message("ok", wall, brief=brief), state={"brief": brief})
        except Exception as exc:  # noqa: BLE001 - any SDK, network or quota failure is reported, never fatal
            log.warning("studying the recording failed: %s", exc)
            left = MAX_STUDY_TRIES - self._study_tries[1]
            note = f" Trying again in {STUDY_RETRY_S:.0f} s." if left > 0 else " Record again to retry."
            self._finish(self._task_message("error", wall, detail=_short_reason(exc) + note),
                         state={"study_failed": True})

    def _reference_pictures(self) -> list[Any]:
        marks = task.waypoint_marks(self._points, self._events)
        wanted = sorted({0, *marks, *(i - 1 for i, kind in marks.items() if kind == "grasp")} & set(self._keyframes))
        parts: list[Any] = []
        for index in wanted[:REFERENCE_PICTURES]:
            parts += [f"From the demonstration, waypoint {index}" + (f" ({marks[index]})" if index in marks else "") + ":",
                      self._keyframes[index]]
        return parts

    def _turn(self, key: tuple[Any, int], arm: dict[str, Any] | None, result: dict[str, Any] | None,
              vision: dict[str, Any] | None) -> None:
        assert self._analyzer is not None and self._brief is not None
        run, seq = key
        started = time.monotonic()
        try:
            picture = self._frames.latest() or self._grab()
        except (OSError, ValueError) as exc:
            log.warning("no camera picture: %s", exc)
            self._finish((agent.TOPIC, agent.build_agent_message(self._now(), run=run, seq=seq, status="error",
                                                                 detail="No camera picture. Is vision running?", model=self.model)),
                         state={"error": True})
            return
        parts = self._reference_pictures()
        if self._previous_picture is not None:
            parts += ["The camera before your last action:", self._previous_picture]
        parts += ["The camera now:", picture,
                  agent.build_turn_prompt(self._brief, self._points, self._events, self._history, arm, result, vision, seq)]
        try:
            action = agent.normalize_action(self._analyzer.generate(parts, agent.AGENT_SYSTEM, agent.ACTION_SCHEMA))
            log.info("turn %s in %.1f s: %s (%s)", seq, time.monotonic() - started, agent.describe_action(action), action["why"])
            self._finish(state={"answer": (key, action), "picture": picture})
        except Exception as exc:  # noqa: BLE001
            log.warning("turn %s failed: %s", seq, exc)
            self._finish((agent.TOPIC, agent.build_agent_message(self._now(), run=run, seq=seq, status="error",
                                                                 detail=_short_reason(exc), model=self.model)),
                         state={"error": True})

    def poll(self) -> list[Message]:
        """Whatever the worker finished, once."""
        with self._lock:
            finished, self._finished = self._finished, []
        if not finished:
            return []
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        out: list[Message] = []
        now = self._now()
        for topic, message in finished:
            if topic:
                out.append((topic, message))
                if topic == task.TOPIC:
                    self.task, self._task_sent = message, now
                continue
            if "brief" in message:
                self._brief = message["brief"]
            if message.get("study_failed"):
                self._studied, self._study_retry_at = None, now + STUDY_RETRY_S
            if message.get("error"):
                self._errors += 1
                self._retry_at = now + self._retry_s
            if "answer" in message:
                key, action = message["answer"]
                self._errors = 0
                self._previous_picture = message["picture"]
                if key[0] == self._run:  # A run that ended while Gemini was thinking gets no answer.
                    out.append(self._answer_with(key, action, now))
        return out

    def wait(self, timeout_s: float) -> list[Message]:
        """Block until the worker finishes (for --once and tests)."""
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self.poll()
