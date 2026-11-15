"""The assistant's state: one Gemini request at a time, on a worker thread, with a cooldown.

The main loop owns the sockets; this class only returns the messages to publish, so it is testable without ZMQ.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Protocol

from .analysis import build_message, build_prompt, normalize_result

log = logging.getLogger(__name__)


class Analyzer(Protocol):
    model: str

    def analyze(self, image: bytes, prompt: str) -> Any: ...


class Assistant:
    def __init__(
        self,
        analyzer: Analyzer | None,
        grab: Callable[[], bytes],
        known: Callable[[], list[str]],
        *,
        cooldown_s: float = 3.0,
        unavailable: str = "Gemini is not configured.",
        now: Callable[[], float] = time.time,
    ) -> None:
        self._analyzer = analyzer
        self._grab = grab
        self._known = known
        self._cooldown_s = cooldown_s
        self._unavailable = unavailable
        self._now = now
        self._seq = 0
        self._started_at: float | None = None
        self._thread: threading.Thread | None = None
        self._finished: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self.last: dict[str, Any] | None = None
        # The last good answer and when it came, so the next prompt can follow up (a nudge with no response).
        self._previous: tuple[dict[str, Any], float] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _message(self, status: str, **kwargs: Any) -> dict[str, Any]:
        self._seq += 1
        model = self._analyzer.model if self._analyzer else None
        message = build_message(self._seq, self._now(), status, model=model, **kwargs)
        self.last = message
        return message

    def request(self, vision: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Start an analysis. Returns what to publish now: `working`, an error, or nothing (busy, cooling down)."""
        if self._analyzer is None:
            return [self._message("error", detail=self._unavailable)]
        if self.busy:
            log.info("request ignored: an analysis is already running")
            return []
        if self._started_at is not None and self._now() - self._started_at < self._cooldown_s:
            log.info("request ignored: within the %.0f s cooldown", self._cooldown_s)
            return []
        self._started_at = self._now()
        self._thread = threading.Thread(target=self._work, args=(vision,), name="assist-gemini", daemon=True)
        self._thread.start()
        return [self._message("working", detail="Looking at the scene.")]

    def _work(self, vision: dict[str, Any] | None) -> None:
        assert self._analyzer is not None
        started = time.monotonic()
        try:
            image = self._grab()
        except (OSError, ValueError) as exc:
            log.warning("no camera picture: %s", exc)
            outcome: tuple[str, dict[str, Any]] = ("error", {"detail": "No camera picture. Is vision running?"})
        else:
            try:
                known = self._known()
                previous = self._previous
                prompt = build_prompt(known, vision, *((previous[0], self._now() - previous[1]) if previous else ()))
                raw = self._analyzer.analyze(image, prompt)
                result = normalize_result(raw, known)
                self._previous = (result, self._now())
                outcome = ("ok", {"result": result})
                log.info("analysis in %.1f s: %s", time.monotonic() - started, result["scene"])
            except Exception as exc:  # noqa: BLE001 - any SDK, network or quota failure is reported, never fatal
                log.warning("analysis failed: %s", exc)
                outcome = ("error", {"detail": _short_reason(exc)})
        with self._lock:
            self._finished = {"status": outcome[0], **outcome[1]}

    def poll(self) -> list[dict[str, Any]]:
        """The finished analysis, once, when the worker is done."""
        with self._lock:
            finished, self._finished = self._finished, None
        if finished is None:
            return []
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        return [self._message(finished.pop("status"), **finished)]

    def wait(self, timeout_s: float) -> list[dict[str, Any]]:
        """Block until the worker finishes (for --once and tests)."""
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self.poll()


def _short_reason(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if "api key" in lowered or "api_key" in lowered or "permission" in lowered or "401" in text or "403" in text:
        return "Gemini refused the API key. Check GEMINI_API_KEY in .env."
    if "429" in text or "quota" in lowered or "resource_exhausted" in lowered:
        return "Gemini quota or rate limit reached. Try again shortly."
    if "404" in text and "model" in lowered:
        return "The Gemini model was not found. Pass another with --model."
    if "503" in text or "unavailable" in lowered:
        return "Gemini is overloaded right now. Try again in a moment."
    if "timeout" in lowered or "timed out" in lowered:
        return "Gemini did not answer in time."
    return f"Analysis failed: {text[:160]}"
