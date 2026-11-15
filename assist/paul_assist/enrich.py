"""More views of a taught object, written by Gemini.

Vision learns an object from one snapshot (or one description), so it knows that object from one side in one
light. Each name can hold several views, and a view may be a short description. So for every taught name that has
not been through here, Gemini is shown the snapshot and asked how the object looks from other sides and in other
light, and its descriptions are sent to vision as one `tag_text` command with `prompts`: vision adds them all to
the name in a single change.

Vision owns the label library; this only reads it. What it has dealt with is kept beside the library in
`enriched.json`, so a name is enriched once, also across restarts.
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

log = logging.getLogger(__name__)

STATE_FILE = "enriched.json"
MAX_ATTEMPTS = 2         # tries per name (Gemini failing, or vision never taking the descriptions) before giving up
SCAN_S = 2.0             # how often the library is read for names that need descriptions
CONFIRM_S = 150.0        # how long vision may take to make the descriptions live (labels.worker_timeout_s is 120)
LABELS_FRESH_S = 10.0    # a `labels` message older than this does not say what vision is doing now
MAX_PICTURE_BYTES = 8 * 1024 * 1024

ENRICH_INSTRUCTION = (
    "You write search phrases for an open-vocabulary object detector on a robot arm's wrist camera. The detector "
    "matches a short English noun phrase against regions of the picture. It already knows one object from a single "
    "snapshot, so it recognises it from that side and in that light only. You describe how the same object looks "
    "otherwise, so it is still found when the camera moves or the light changes.\n"
    "Rules for every phrase: a plain noun phrase of 3 to 10 words naming what is visible (the kind of object, its "
    "shape, colours, material, lid, label or markings). No brand names unless printed large on the object, no "
    "verbs, no sentences, no mention of the camera, the robot, the box or the snapshot. Each phrase must describe "
    "the object alone and differ from the others in what it shows: another side, the top, the object lying down, "
    "or its colours in dim or harsh light. Never describe the background or anything else in the picture."
)

ENRICH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object": {"type": "string", "description": "What the object is, in a few words."},
        "prompts": {"type": "array", "items": {"type": "string"},
                    "description": "The search phrases, most useful first."},
    },
    "required": ["object", "prompts"],
}


@dataclass(frozen=True)
class TagInfo:
    """One taught name, as read from vision's library.json."""
    name: str
    views: int
    first_view: str                                            # the id of its first view: tells a re-taught name apart
    prompts: tuple[str, ...]                                   # the descriptions it already has
    image: Path | None = None                                  # its newest snapshot, if it has one
    bbox: tuple[float, float, float, float] | None = None      # the object in that snapshot, in pixels

    @property
    def key(self) -> str:
        return f"{self.name}|{self.first_view}"


@dataclass(frozen=True)
class LibraryInfo:
    tags: tuple[TagInfo, ...] = ()
    max_views: int = 5
    max_prompt_chars: int = 100
    directory: Path | None = None


def read_library(vision_config: Path) -> LibraryInfo | None:
    """The taught names and vision's limits, from its config and library.json. None (with a warning) if the config
    cannot be read; no tags if there is no library yet or it cannot be read right now."""
    try:
        cfg = yaml.safe_load(vision_config.read_text(encoding="utf-8"))
        labels = cfg["labels"]
        directory = Path(labels["dir"])
        if not directory.is_absolute():
            directory = vision_config.parent / directory
        max_views, max_chars = int(labels["max_views"]), int(labels["max_prompt_chars"])
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        log.warning("cannot read the label settings from %s: %s", vision_config, exc)
        return None
    tags: list[TagInfo] = []
    try:
        data = json.loads((directory / "library.json").read_text(encoding="utf-8"))
        for tag in data.get("tags", []):
            views = tag["views"]
            pictures = [v for v in views if v.get("kind") == "visual" and v.get("image") and v.get("bbox")]
            newest = pictures[-1] if pictures else None
            tags.append(TagInfo(
                name=str(tag["name"]), views=len(views), first_view=str(views[0]["id"]),
                prompts=tuple(str(v["prompt"]) for v in views if v.get("prompt")),
                image=directory / newest["image"] if newest else None,
                bbox=tuple(float(c) for c in newest["bbox"]) if newest else None))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        log.warning("cannot read the taught names from %s: %s", directory / "library.json", exc)
        tags = []
    return LibraryInfo(tuple(tags), max_views, max_chars, directory)


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) of a PNG, from its header. ValueError if it is not one."""
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise ValueError("not a PNG picture")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ValueError("the PNG has no size")
    return width, height


def box_1000(bbox: tuple[float, float, float, float], size: tuple[int, int]) -> list[int]:
    """A pixel box [x1, y1, x2, y2] as Gemini's [ymin, xmin, ymax, xmax] on a 0..1000 scale."""
    (x1, y1, x2, y2), (width, height) = bbox, size

    def scale(value: float, side: int) -> int:
        return max(0, min(1000, round(1000 * value / side)))

    return [scale(y1, height), scale(x1, width), scale(y2, height), scale(x2, width)]


def build_enrich_prompt(name: str, existing: tuple[str, ...], count: int, box: list[int] | None) -> str:
    lines = [f'The operator calls this object "{name}".']
    if box is not None:
        lines.append(f"It is the object inside the box [ymin, xmin, ymax, xmax] = {box} of the attached picture "
                     f"(coordinates on a 0 to 1000 scale). The picture is the one snapshot the detector learned it "
                     f"from. Look only at that object.")
    else:
        lines.append("There is no picture of it. Work from the name and the descriptions below, and from what such "
                     "an object usually looks like.")
    if existing:
        lines.append("The detector already has these descriptions; do not repeat them: "
                     + "; ".join(f'"{p}"' for p in existing) + ".")
    lines.append(f"Write {count} phrases for how this same object looks from other sides, from above, lying down, "
                 f"and in dim or harsh light.")
    return "\n".join(lines)


def normalize_prompts(raw: Any, existing: tuple[str, ...], count: int, max_chars: int) -> list[str]:
    """Gemini's answer as at most `count` usable phrases: single-spaced, short enough for vision, not empty, not one
    the name already has, no repeats. [] for an answer of another shape."""
    answers = raw.get("prompts") if isinstance(raw, dict) else None
    if not isinstance(answers, list):
        return []
    seen = {p.lower() for p in existing}
    out: list[str] = []
    for answer in answers:
        if not isinstance(answer, str):
            continue
        prompt = " ".join(answer.split()).strip(" .\"'")
        if not prompt or len(prompt) > max_chars or not prompt.isprintable() or prompt.lower() in seen:
            continue
        seen.add(prompt.lower())
        out.append(prompt)
        if len(out) == count:
            break
    return out


def load_state(path: Path) -> dict[str, str]:
    """{tag key: "done" | "failed"}; {} if the file is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, str]) -> None:
    try:
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("cannot save %s (the names in it may be described again after a restart): %s", path, exc)


class Describer(Protocol):
    def generate(self, parts: list[Any], system: str, schema: dict[str, Any]) -> Any: ...


@dataclass
class _Batch:
    tag: TagInfo
    prompts: list[str]
    sent_at: float | None = None
    views_before: int = 0
    sends: int = 0


@dataclass
class Enricher:
    """Finds taught names without Gemini's descriptions, asks for them one name at a time on a worker thread, and
    hands them to `send` when vision is free. The main loop calls poll() on every pass; it returns at once.

    `send(command)` publishes a `command` payload to vision and returns False if vision is not listening.
    """
    analyzer: Describer | None
    library: Callable[[], LibraryInfo | None]
    send: Callable[[dict[str, Any]], bool]
    count: int = 3
    now: Callable[[], float] = time.monotonic
    _state: dict[str, str] | None = None
    _attempts: dict[str, int] = field(default_factory=dict)
    _thread: threading.Thread | None = None
    _finished: tuple[TagInfo, list[str] | None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _batch: _Batch | None = None
    _last_scan: float = float("-inf")
    _labels: tuple[float, dict[str, Any]] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None or self._batch is not None

    def saw_labels(self, message: dict[str, Any]) -> None:
        """Vision's newest `labels` message: whether it is busy with a name, and how many views each name has."""
        self._labels = (self.now(), message)

    def _vision_idle(self) -> bool:
        if self._labels is None or self.now() - self._labels[0] > LABELS_FRESH_S:
            return False
        return self._labels[1].get("busy") is None

    def _views_live(self, name: str) -> int | None:
        if self._labels is None:
            return None
        for tag in self._labels[1].get("tags") or []:
            if isinstance(tag, dict) and tag.get("name") == name:
                return tag.get("views") if isinstance(tag.get("views"), int) else None
        return 0  # vision does not list the name: it was forgotten

    def _mark(self, info: LibraryInfo, tag: TagInfo, outcome: str) -> None:
        assert self._state is not None and info.directory is not None
        self._state[tag.key] = outcome
        save_state(info.directory / STATE_FILE, self._state)

    def _failed(self, info: LibraryInfo, tag: TagInfo, why: str) -> None:
        self._attempts[tag.key] = self._attempts.get(tag.key, 0) + 1
        if self._attempts[tag.key] >= MAX_ATTEMPTS:
            log.warning("no extra descriptions for '%s' after %d tries (%s); leaving it as it is", tag.name,
                        MAX_ATTEMPTS, why)
            self._mark(info, tag, "failed")
        else:
            log.info("extra descriptions for '%s' failed (%s); trying once more", tag.name, why)

    def poll(self) -> None:
        if self.analyzer is None:
            return
        with self._lock:
            finished, self._finished = self._finished, None
        if finished is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
            tag, prompts = finished
            if prompts:
                self._batch = _Batch(tag, prompts)
            else:
                info = self.library()
                if info is not None and info.directory is not None:
                    self._failed(info, tag, "Gemini gave nothing usable")
        if self._batch is not None:
            self._deliver()
        elif self._thread is None and self.now() - self._last_scan >= SCAN_S:
            self._last_scan = self.now()
            self._scan()

    def _deliver(self) -> None:
        batch = self._batch
        info = self.library()
        if info is None or info.directory is None:
            return
        if self._state is None:
            self._state = load_state(info.directory / STATE_FILE)
        if batch.sent_at is None:
            if not self._vision_idle():
                return  # vision is learning something else, or not running: wait, the descriptions keep
            command = {"t": time.time(), "type": "tag_text", "name": batch.tag.name, "prompts": batch.prompts}
            if not self.send(command):
                return
            batch.sent_at, batch.sends = self.now(), batch.sends + 1
            batch.views_before = self._views_live(batch.tag.name) or batch.tag.views
            log.info("sent %d extra description(s) for '%s': %s", len(batch.prompts), batch.tag.name, batch.prompts)
            return
        live = self._views_live(batch.tag.name)
        if live is not None and live > batch.views_before:
            log.info("'%s' now has %d views", batch.tag.name, live)
            self._mark(info, batch.tag, "done")
            self._batch = None
        elif live == 0:
            log.info("'%s' was forgotten meanwhile; its descriptions are dropped", batch.tag.name)
            self._batch = None
        elif self.now() - batch.sent_at > CONFIRM_S:
            if batch.sends >= MAX_ATTEMPTS:
                log.warning("vision never added the descriptions for '%s'; leaving it as it is", batch.tag.name)
                self._mark(info, batch.tag, "failed")
                self._batch = None
            else:
                batch.sent_at = None  # vision was busy with another command at that moment: send once more

    def _scan(self) -> None:
        info = self.library()
        if info is None or info.directory is None:
            return
        if self._state is None:
            self._state = load_state(info.directory / STATE_FILE)
        busy_with = self._labels[1].get("busy") if self._labels is not None else None
        for tag in info.tags:
            if tag.key in self._state or tag.name == busy_with:
                continue
            want = min(self.count, info.max_views - tag.views)
            if want <= 0:
                self._mark(info, tag, "done")  # no room for more views: nothing to add
                continue
            self._thread = threading.Thread(target=self._work, args=(tag, want, info.max_prompt_chars),
                                            name="assist-enrich", daemon=True)
            self._thread.start()
            return

    def _work(self, tag: TagInfo, want: int, max_chars: int) -> None:
        prompts: list[str] | None = None
        started = time.monotonic()
        try:
            parts: list[Any] = []
            box = None
            if tag.image is not None and tag.bbox is not None:
                picture = tag.image.read_bytes()
                if len(picture) > MAX_PICTURE_BYTES:
                    raise ValueError("the snapshot is larger than 8 MB")
                box = box_1000(tag.bbox, png_size(picture))
                parts.append(picture)
            parts.append(build_enrich_prompt(tag.name, tag.prompts, want, box))
            raw = self.analyzer.generate(parts, ENRICH_INSTRUCTION, ENRICH_SCHEMA)
            prompts = normalize_prompts(raw, tag.prompts, want, max_chars)
            log.info("Gemini described '%s' in %.1f s: %s", tag.name, time.monotonic() - started, prompts)
        except Exception as exc:  # noqa: BLE001 - any file, SDK, network or quota failure: reported, never fatal
            log.warning("describing '%s' failed: %s", tag.name, str(exc)[:200])
        with self._lock:
            self._finished = (tag, prompts)

    def wait(self, timeout_s: float) -> None:
        """Block until the worker finishes, then poll (for tests)."""
        if self._thread is not None:
            self._thread.join(timeout_s)
        self.poll()
