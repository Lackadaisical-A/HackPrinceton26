"""The label library: names the operator taught vision while it ran (M4).

Importable without torch, Ultralytics or OpenCV: the voice process reads the names from here, and the offline
commands below only need numpy.

    <labels dir>/library.json             the index, always written atomically (temp file, then os.replace)
    <labels dir>/views-<hash>.npz         one prompt embedding per view, named by its content
    <labels dir>/refs/<view id>.png       the picture each visual view was taken from (for rebuilding)
    <labels dir>/openvino/<key>_openvino_model/   the OpenVINO export for the current class list
    <labels dir>/library.next.json        a change that is built but not live yet, or live but not saved (see below)

A tag is a name with one or more views. A view is a box on a picture (tag_visual) or a text prompt (tag_text); each
view is one class inside the model, called `<name>#<k>`. Every label that leaves the detector is the name alone
(`public_labels`). A name may equal one of detector.classes: its views are then extra classes for the same label,
and removing the tag removes only those views.

Only one process may change the library at a time. While vision runs, that is its label worker
(python -m paul_vision.label_worker): the worker writes a change to library.next.json, and vision renames it to
library.json once the model that contains the change serves frames, so library.json holds what vision detects now (if
the rename fails, vision tries again before the next change: label_manager). The offline commands below write
library.json directly instead: `remove` itself, `rebuild` through the same worker. Neither checks whether vision runs,
so use them only while it is stopped.

    uv run python -m paul_vision.labels list
    uv run python -m paul_vision.labels remove "my flask"     # while vision is stopped (running: send untag)
    uv run python -m paul_vision.labels rebuild               # embeddings again from the saved pictures and prompts
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config

log = logging.getLogger(__name__)

LIBRARY_FILE = "library.json"
NEXT_FILE = "library.next.json"
LIBRARY_VERSION = 1
VIEW_SEP = "#"               # model class name of a view: "<name>#<k>"
REFS_DIR = "refs"
EXPORTS_DIR = "openvino"
JOBS_DIR = "jobs"
KIND_VISUAL = "visual"
KIND_TEXT = "text"
EMBED_DIM = 512


class LabelError(ValueError):
    """A change the library cannot take. The message is a short sentence an operator can hear."""


@dataclass(frozen=True)
class View:
    id: str                                    # unique in the library; names the picture and the embedding row
    kind: str                                  # visual | text
    added: float                               # Unix time
    prompt: str | None = None                  # text views
    image: str | None = None                   # visual views: refs/<id>.png, relative to the library folder
    bbox: tuple[float, float, float, float] | None = None   # visual views: the box in pixels of that picture


@dataclass(frozen=True)
class Tag:
    name: str
    views: tuple[View, ...]


@dataclass(frozen=True)
class Library:
    weights: str | None = None                 # checkpoint file name the embeddings were made with
    imgsz: tuple[int, int] | None = None       # detector.imgsz they were made at (it changes visual embeddings)
    embeddings: str | None = None              # the views npz, a file name in the library folder
    tags: tuple[Tag, ...] = ()

    @property
    def views(self) -> list[tuple[str, View]]:
        """(name, view) in model order."""
        return [(t.name, v) for t in self.tags for v in t.views]

    def tag(self, name: str) -> Tag | None:
        return next((t for t in self.tags if t.name == name), None)


EMPTY = Library()


def library_dir(cfg: Config) -> Path:
    """The configured library folder. `load_config` resolves `labels.dir` next to the config file, as it does
    `detector.models_dir`; a config built in code keeps a relative path relative to the working directory."""
    return Path(cfg.labels.dir)


# ---------------------------------------------------------------------------------------------------- names

def normalize_name(text: str) -> str:
    """Lower case, single spaces, no space at either end."""
    return " ".join(str(text).lower().split())


def name_problem(name: str, *, max_chars: int, reserved: Collection[str] = ()) -> str | None:
    """Why `name` (already normalized) cannot be a tag name, as a short sentence; None if it can."""
    if not name:
        return "The name is empty."
    if len(name) > max_chars:
        return "That name is too long."
    if "/" in name or VIEW_SEP in name:
        return "Names cannot contain a slash or a hash sign."
    if not name.isprintable():
        return "That name has characters that cannot be used."
    if name in reserved:
        return "That name belongs to a station."
    return None


def view_class(name: str, k: int) -> str:
    """The class name of a tag's k-th view inside the model."""
    return f"{name}{VIEW_SEP}{k}"


def label_of(model_class: str) -> str:
    """The public label for a model class name: `my flask#1` -> `my flask`; anything else unchanged."""
    return model_class.split(VIEW_SEP, 1)[0]


def class_names(base: Sequence[str], lib: Library) -> list[str]:
    """The model's class list: the configured classes, then one class per view."""
    return list(base) + [view_class(t.name, k) for t in lib.tags for k in range(len(t.views))]


def public_labels(base: Sequence[str], lib: Library) -> list[str]:
    """The label each model class is published as, in class order."""
    return list(base) + [t.name for t in lib.tags for _ in t.views]


def known_labels(base: Sequence[str], lib: Library) -> list[str]:
    """Every label the detector can report: the configured classes, then the tag names that are not among them."""
    return list(base) + [t.name for t in lib.tags if t.name not in base]


# ---------------------------------------------------------------------------------------------------- changes

def with_view(lib: Library, name: str, view: View, *, max_tags: int, max_views: int) -> Library:
    """The library with one more view for `name` (a new tag if the name is new). LabelError past the limits."""
    tag = lib.tag(name)
    if tag is None:
        if len(lib.tags) >= max_tags:
            raise LabelError(f"The library is full ({max_tags} names).")
        return dataclasses.replace(lib, tags=lib.tags + (Tag(name, (view,)),))
    if len(tag.views) >= max_views:
        raise LabelError(f"{name.capitalize()} already has {max_views} views.")
    tags = tuple(Tag(t.name, t.views + (view,)) if t.name == name else t for t in lib.tags)
    return dataclasses.replace(lib, tags=tags)


def without_tag(lib: Library, name: str) -> Library:
    """The library without `name` and all its views. LabelError if there is no such tag."""
    if lib.tag(name) is None:
        raise LabelError(f"Nothing was learned as {name}.")
    return dataclasses.replace(lib, tags=tuple(t for t in lib.tags if t.name != name))


def check_limits(lib: Library, *, max_tags: int, max_views: int) -> None:
    if len(lib.tags) > max_tags:
        raise ValueError(f"the library has {len(lib.tags)} names; labels.max_tags is {max_tags}")
    for t in lib.tags:
        if len(t.views) > max_views:
            raise ValueError(f"'{t.name}' has {len(t.views)} views; labels.max_views is {max_views}")


def new_view_id() -> str:
    return os.urandom(6).hex()


# ---------------------------------------------------------------------------------------------------- files

REPLACE_TRIES = 5          # Windows refuses to replace a file another process has open for a moment (the voice
REPLACE_PAUSE_S = 0.01     # process reads the names every few seconds): try again, at most ~0.05 s in all


def _replace(src: Path, dst: Path) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    _replace(tmp, path)


def _to_json(lib: Library) -> dict:
    def view(v: View) -> dict:
        out = {"id": v.id, "kind": v.kind, "added": round(v.added, 3)}
        if v.kind == KIND_TEXT:
            out["prompt"] = v.prompt
        else:
            out["image"] = v.image
            out["bbox"] = [round(float(c), 2) for c in v.bbox]
        return out

    return {"version": LIBRARY_VERSION, "weights": lib.weights, "imgsz": None if lib.imgsz is None else list(lib.imgsz),
            "embeddings": lib.embeddings,
            "tags": [{"name": t.name, "views": [view(v) for v in t.views]} for t in lib.tags]}


def _parse(data: dict, path: Path) -> Library:
    def fail(why: str):
        raise ValueError(f"{path}: {why}")

    def view(d) -> View:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or d.get("kind") not in (KIND_VISUAL, KIND_TEXT):
            fail("every view needs an `id` and a `kind` (visual or text)")
        added = d.get("added", 0.0)
        if not isinstance(added, (int, float)):
            fail("a view's `added` must be a number")
        if d["kind"] == KIND_TEXT:
            if not isinstance(d.get("prompt"), str) or not d["prompt"]:
                fail(f"text view {d['id']} has no `prompt`")
            return View(d["id"], KIND_TEXT, float(added), prompt=d["prompt"])
        box = d.get("bbox")
        if not isinstance(d.get("image"), str) or not (isinstance(box, list) and len(box) == 4
                                                      and all(isinstance(c, (int, float)) for c in box)):
            fail(f"visual view {d['id']} needs an `image` and a `bbox` of four numbers")
        return View(d["id"], KIND_VISUAL, float(added), image=d["image"], bbox=tuple(float(c) for c in box))

    tags = []
    for t in data["tags"]:
        views = t.get("views", [])
        if not isinstance(views, list) or not views:
            fail(f"tag '{t['name']}' has no views")
        tags.append(Tag(t["name"], tuple(view(v) for v in views)))
    names = [t.name for t in tags]
    if len(set(names)) != len(names):
        fail("a name appears twice")
    ids = [v.id for t in tags for v in t.views]
    if len(set(ids)) != len(ids):
        fail("a view id appears twice")
    imgsz = data.get("imgsz")
    if imgsz is not None and not (isinstance(imgsz, list) and len(imgsz) == 2 and all(isinstance(v, int) for v in imgsz)):
        fail("`imgsz` must be [height, width]")
    for key in ("weights", "embeddings"):
        if data.get(key) is not None and not isinstance(data[key], str):
            fail(f"`{key}` must be a file name")
    if tags and not (data.get("weights") and imgsz and data.get("embeddings")):
        fail("a library with tags needs `weights`, `imgsz` and `embeddings`")
    return Library(data.get("weights"), None if imgsz is None else tuple(imgsz), data.get("embeddings"), tuple(tags))


def _load_index(directory: Path, file: str = LIBRARY_FILE) -> dict | None:
    """The parsed index, None when there is none; ValueError when it cannot be read as a library."""
    path = Path(directory) / file
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a label library (expected a JSON object)")
    if data.get("version") != LIBRARY_VERSION:
        raise ValueError(f"{path} has version {data.get('version')!r}; this code reads version {LIBRARY_VERSION}")
    tags = data.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in tags):
        raise ValueError(f"{path}: `tags` must be a list of objects that each have a `name`")
    return data


def read_tag_names(directory: Path) -> list[str]:
    """The names in the library at `directory`, in the order they were first taught; [] when there is none."""
    data = _load_index(directory)
    return [] if data is None else [t["name"] for t in data["tags"]]


def load_library(directory: Path, file: str = LIBRARY_FILE) -> Library:
    """The library at `directory` (EMPTY when there is none). ValueError if the file is broken or foreign."""
    data = _load_index(directory, file)
    return EMPTY if data is None else _parse(data, Path(directory) / file)


def write_library(directory: Path, lib: Library, file: str = LIBRARY_FILE) -> Path:
    """Write the index atomically: readers see the old file or the new one, never half of one."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file
    _write_atomic(path, (json.dumps(_to_json(lib), indent=1) + "\n").encode("utf-8"))
    return path


def commit_next(directory: Path) -> None:
    """Make the staged change (library.next.json) the library. One rename: cheap enough for the frame thread."""
    _replace(Path(directory) / NEXT_FILE, Path(directory) / LIBRARY_FILE)


def discard_next(directory: Path) -> None:
    """Drop a staged change. One that cannot be deleted now (Windows: another thread still reads it) is left; it is
    never committed by itself (every worker writes its own), and the next start discards it."""
    try:
        (Path(directory) / NEXT_FILE).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("could not delete the staged label change %s yet: %s", Path(directory) / NEXT_FILE, exc)


# ---------------------------------------------------------------------------------------------------- embeddings

def save_view_embeddings(directory: Path, ids: Sequence[str], embeddings: np.ndarray) -> str:
    """Write one row per view id to views-<hash>.npz (atomically) and return the file name. The name comes from the
    content, so a changed library never reuses an older file by accident."""
    emb = np.ascontiguousarray(embeddings, dtype=np.float32).reshape(len(ids), EMBED_DIM)
    if not np.isfinite(emb).all():
        raise ValueError("view embeddings must be finite")
    digest = hashlib.sha1()
    digest.update(json.dumps(list(ids)).encode("utf-8"))
    digest.update(emb.tobytes())
    name = f"views-{digest.hexdigest()[:12]}.npz"
    path = Path(directory) / name
    if not path.exists():
        tmp = path.with_name(f".{name}.{os.getpid()}.tmp.npz")
        np.savez_compressed(tmp, ids=np.asarray(list(ids), dtype=np.str_), embeddings=emb)
        os.replace(tmp, path)
    return name


def load_view_embeddings(directory: Path, lib: Library) -> np.ndarray:
    """(views, 512) float32 in model order. ValueError if the file does not match the library."""
    if not lib.tags:
        return np.zeros((0, EMBED_DIM), np.float32)
    path = Path(directory) / lib.embeddings
    try:
        with np.load(path, allow_pickle=False) as d:
            ids, emb = [str(i) for i in d["ids"]], d["embeddings"]
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"cannot read the view embeddings {path}: {exc}") from exc
    rows = {i: n for n, i in enumerate(ids)}
    wanted = [v.id for _, v in lib.views]
    missing = [i for i in wanted if i not in rows]
    if missing or emb.ndim != 2 or emb.shape[1] != EMBED_DIM or emb.dtype != np.float32:
        raise ValueError(f"{path} does not hold the embeddings of every view in the library")
    return emb[[rows[i] for i in wanted]]


def subset_embeddings(directory: Path, old: Library, new: Library) -> Library:
    """`new` (fewer views than `old`) with its own views file, cut from old's. Numpy only."""
    if not new.tags:
        return dataclasses.replace(new, embeddings=None, weights=None, imgsz=None)
    old_emb = load_view_embeddings(directory, old)
    rows = {v.id: n for n, (_, v) in enumerate(old.views)}
    ids = [v.id for _, v in new.views]
    name = save_view_embeddings(directory, ids, old_emb[[rows[i] for i in ids]])
    return dataclasses.replace(new, embeddings=name)


def export_key(lib: Library, weights: str, imgsz: Sequence[int], base: Sequence[str]) -> str:
    """Key of the OpenVINO export for this class list: weights, input size, every class name and the view embeddings
    (the views file is named by its content). Base embeddings are fixed by weights and base names."""
    payload = json.dumps({"weights": Path(weights).name, "imgsz": list(imgsz), "classes": class_names(base, lib),
                          "views": lib.embeddings}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def export_dir(directory: Path, key: str) -> Path:
    # Ultralytics recognises an OpenVINO model by the folder suffix.
    return Path(directory) / EXPORTS_DIR / f"{key}_openvino_model"


def export_complete(path: Path) -> bool:
    """True if `path` holds a whole OpenVINO export (prune() can leave half of one behind on Windows, where a file a
    running model still holds cannot be deleted)."""
    path = Path(path)
    return (path / "metadata.yaml").is_file() and any(path.glob("*.xml")) and any(path.glob("*.bin"))


@dataclass(frozen=True, eq=False)
class ModelViews:
    """What the detector needs to detect a library's views, after detector.classes."""
    classes: tuple[str, ...]          # model class names, `<name>#<k>`
    labels: tuple[str, ...]           # the label each is published as
    embeddings: np.ndarray            # (views, 512) float32: the torch profile sets these
    export: str | None = None         # the OpenVINO export with detector.classes and these views built in


def model_views(directory: Path, lib: Library, export: str | Path | None = None) -> ModelViews:
    """The views of `lib` for the detector. ValueError if the views file does not match the library."""
    return ModelViews(tuple(class_names((), lib)), tuple(public_labels((), lib)), load_view_embeddings(directory, lib),
                      None if export is None else str(export))


def stale_reason(lib: Library, weights: str, imgsz: Sequence[int]) -> str | None:
    """Why the library's embeddings cannot be used with these weights and input size; None if they can."""
    if not lib.tags:
        return None
    if lib.weights != Path(weights).name:
        return f"it was made with {lib.weights}, and the detector now uses {Path(weights).name}"
    if list(lib.imgsz) != list(imgsz):
        return f"it was made at input size {list(lib.imgsz)}, and detector.imgsz is now {list(imgsz)}"
    return None


def prune(directory: Path, keep: Sequence[Library], keep_exports: Collection[str] = ()) -> list[str]:
    """Delete pictures, view files and exports that none of `keep` uses (left by failed or replaced changes).
    Returns what was deleted. Anything that cannot be deleted now (in use) is left for the next time."""
    directory = Path(directory)
    images = {v.image for lib in keep for _, v in lib.views if v.image}
    npzs = {lib.embeddings for lib in keep if lib.embeddings}
    exports = {f"{k}_openvino_model" for k in keep_exports}
    removed = []
    candidates = [p for p in (directory / REFS_DIR).glob("*") if f"{REFS_DIR}/{p.name}" not in images]
    candidates += [p for p in directory.glob("views-*.npz") if p.name not in npzs]
    candidates += [p for p in directory.glob(".*.tmp*")]
    candidates += [p for p in (directory / EXPORTS_DIR).glob("*") if p.name not in exports]
    for path in candidates:
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            removed.append(str(path.relative_to(directory)))
        except OSError as exc:
            log.debug("could not delete %s yet: %s", path, exc)
    return removed


# ---------------------------------------------------------------------------------------------------- offline CLI

def _cmd_list(directory: Path, cfg: Config) -> int:
    lib = load_library(directory)
    if not lib.tags:
        print(f"no tags in {directory}")
        return 0
    print(f"{len(lib.tags)} tag(s) in {directory} (made with {lib.weights} at {list(lib.imgsz)})")
    for t in lib.tags:
        what = ", ".join(f"picture {v.image}" if v.kind == KIND_VISUAL else f"text '{v.prompt}'" for v in t.views)
        extra = "  (adds views to a configured class)" if t.name in cfg.detector.classes else ""
        print(f"  {t.name}: {len(t.views)} view(s): {what}{extra}")
    return 0


def _cmd_remove(directory: Path, name: str) -> int:
    name = normalize_name(name)
    lib = load_library(directory)
    try:
        new = subset_embeddings(directory, lib, without_tag(lib, name))
    except LabelError as exc:
        print(exc, file=sys.stderr)
        return 1
    write_library(directory, new)
    discard_next(directory)
    later = " On OpenVINO, the export for the tags that remain is built at the next start." if new.tags else ""
    print(f"removed '{name}'.{later}")
    prune(directory, [new])
    return 0


def _cmd_rebuild(cfg: Config, backend: str) -> int:
    from . import label_manager

    lib = load_library(library_dir(cfg))
    if not lib.tags:
        print("no tags: nothing to rebuild")
        return 0
    discard_next(library_dir(cfg))
    result = label_manager.run_blocking(cfg, "rebuild", backend=backend)
    print(f"rebuilt {len(lib.tags)} tag(s) in {library_dir(cfg)} for {backend} "
          f"({', '.join(f'{k} {v:.1f} s' for k, v in result['timings'].items())})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.labels",
                                     description="List, remove or rebuild the operator's tags.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the tags and their views")
    rm = sub.add_parser("remove", help="forget a tag (only while vision is stopped; while it runs, send untag)")
    rm.add_argument("name", nargs="+")
    rb = sub.add_parser("rebuild", help="make every embedding again from the saved pictures and prompts")
    rb.add_argument("--backend", choices=["torch", "openvino"], default=None,
                    help="openvino also builds the export (default: detector.backend; auto counts as openvino here)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"configuration: {exc}", file=sys.stderr)
        return 2
    directory = library_dir(cfg)
    try:
        if args.cmd == "list":
            return _cmd_list(directory, cfg)
        if args.cmd == "remove":
            return _cmd_remove(directory, " ".join(args.name))
        backend = args.backend or ("torch" if cfg.detector.backend == "torch" else "openvino")
        return _cmd_rebuild(cfg, backend)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
