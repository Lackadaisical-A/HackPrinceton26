"""The one call to Gemini: a picture and a prompt in, structured JSON out."""

from __future__ import annotations

import json
import logging
from typing import Any

from .analysis import RESPONSE_SCHEMA, SYSTEM_INSTRUCTION

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
# Tried in order after the chosen model when it is overloaded (503), rate-limited (429) or retired (404).
FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite")
_RETRYABLE = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND")


def is_retryable(exc: Exception) -> bool:
    text = str(exc)
    return any(code in text for code in _RETRYABLE)


def image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise ValueError("the picture is neither JPEG nor PNG")


class GeminiAnalyzer:
    """Wraps google-genai. Imported lazily so tests and --help need no SDK."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout_s: float = 30.0) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key,
                                    http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self.model = model
        self._models = list(dict.fromkeys([model, *FALLBACK_MODELS]))

    def analyze(self, image: bytes, prompt: str) -> Any:
        """Gemini's parsed JSON answer about one picture. See generate."""
        return self.generate([image, prompt], SYSTEM_INSTRUCTION, RESPONSE_SCHEMA)

    def generate(self, parts: list[Any], system: str, schema: dict[str, Any]) -> Any:
        """Gemini's parsed JSON answer to text and pictures in order (str = text, bytes = JPEG or PNG), from
        the first model that answers. Raises the last error if none does.

        `model` is set to the one that answered, so the console shows which model it was.
        """
        error: Exception | None = None
        for model in self._models:
            try:
                answer = self._ask(model, parts, system, schema)
            except Exception as exc:  # noqa: BLE001 - re-raised below unless another model answers
                error = exc
                if not is_retryable(exc):
                    raise
                log.warning("%s unavailable (%s); trying the next model", model, str(exc)[:80])
                continue
            self.model = model
            return answer
        assert error is not None
        raise error

    def _ask(self, model: str, parts: list[Any], system: str, schema: dict[str, Any]) -> Any:
        types = self._types
        response = self._client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=p, mime_type=image_mime(p)) if isinstance(p, bytes) else p
                      for p in parts],
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2,
            ),
        )
        text = response.text
        if not text:
            raise ValueError("Gemini returned no answer (it may have been blocked)")
        return json.loads(text)
