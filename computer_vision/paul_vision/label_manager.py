"""Label changes while vision runs (M4): tag_visual, tag_text and untag, one at a time, without stalling a frame.

Every change takes the same path:

    teach()   checks the request against the latest processed frame and the library, answers at once
              ("Learning my flask." or a reason), saves the picture and starts the label worker
              (python -m paul_vision.label_worker, a child process at low priority)
    poll()    on every pass of the main loop: has the worker finished? Then a thread loads the result into the
              detector (UltralyticsDetector.prepare: on OpenVINO the new export is compiled and warmed up while the
              old model keeps serving). Once that is done, between two frames: adopt() swaps the class list,
              library.next.json becomes library.json, the tracker forgets its label votes, and "My flask is ready."

So a name is announced only once the model that contains it serves frames, and library.json always holds what vision
detects now. A worker that fails, or takes longer than labels.worker_timeout_s, is reported and changes nothing.

If library.json cannot be replaced (another process holds it past the retries), the change is live but not saved
("..., but it could not be saved."), and library.next.json keeps it. The save is tried again before the next change
starts, because every worker starts from library.json and deletes the files it does not reference; while the save
still fails, new changes are refused. close() tries once more. teach() and poll() never raise: an unexpected error
ends the change in progress as a failed one.

`prepare_startup` and `run_blocking` are the same worker run in the foreground: at startup (a library made for other
weights or another input size is rebuilt; a missing OpenVINO export is built) and by `python -m paul_vision.labels`.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import clock
from . import labels as lb
from .aruco import STATION_DROP, STATION_PICK
from .commands import TAG_TEXT, TAG_VISUAL, UNTAG, LabelRequest
from .config import Config
from .geometry import iou
from .types import STATUS_OK, Frame

if TYPE_CHECKING:
    from .app import FrameResult
    from .detector import Profile, UltralyticsDetector

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # the worker runs from here, as `python -m`
SOURCE_YOLO = "yolo"
OP_ADD, OP_REMOVE, OP_REBUILD, OP_EXPORT = "add", "remove", "rebuild", "export"
RESERVED_NAMES = (STATION_PICK, STATION_DROP)  # the station labels: a tag must never be mistaken for a station
KEEP_LOGS = 10             # worker logs kept in <labels dir>/jobs
SHUTDOWN_WAIT_S = 5.0      # on the way out: how long to wait for a killed worker or a loading thread
CLI_FIX = ("fix it with `uv run python -m paul_vision.labels rebuild`, or forget a tag with "
           "`uv run python -m paul_vision.labels remove <name>`")


# ---------------------------------------------------------------------------------------------------- the worker

@dataclass(frozen=True)
class Job:
    id: str
    op: str
    name: str | None
    path: Path        # the job file the worker reads
    result: Path      # where it writes its outcome
    log: Path         # its stdout and stderr
    frame: Path | None = None  # tag_visual: the picture, as .npy


def backend_weights(cfg: Config, backend: str) -> str:
    return cfg.detector.weights.torch if backend == "torch" else cfg.detector.weights.openvino


def write_job(cfg: Config, op: str, *, backend: str, commit: bool, low_priority: bool, keep_export: str | None = None,
              name: str | None = None, view: dict | None = None, frame: np.ndarray | None = None) -> Job:
    """Write a job file for the label worker (and the picture for a visual view)."""
    directory = lb.library_dir(cfg).resolve()
    jobs = directory / lb.JOBS_DIR
    jobs.mkdir(parents=True, exist_ok=True)
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
    job = Job(job_id, op, name, jobs / f"{job_id}.json", jobs / f"{job_id}.result.json", jobs / f"{job_id}.log",
              jobs / f"{job_id}.npy" if frame is not None else None)
    det = dataclasses.asdict(cfg.detector)
    det["models_dir"] = str(Path(cfg.detector.models_dir).resolve())  # the worker runs in another folder
    spec = {"op": op, "detector": det, "weights": backend_weights(cfg, backend), "labels_dir": str(directory),
            "max_tags": cfg.labels.max_tags, "max_views": cfg.labels.max_views, "threads": cfg.labels.worker_threads,
            "export": backend == "openvino", "commit": commit, "keep_export": keep_export,
            "low_priority": low_priority, "result": str(job.result)}
    if name is not None:
        spec["name"] = name
    if view is not None:
        spec["view"] = dict(view)
    if frame is not None:
        np.save(job.frame, frame, allow_pickle=False)
        spec["view"]["frame_npy"] = str(job.frame)
    lb._write_atomic(job.path, json.dumps(spec, indent=1).encode("utf-8"))
    return job


def popen_worker(job: Job, low_priority: bool) -> subprocess.Popen:
    """Start the label worker for `job`. Its output goes to job.log (a pipe nobody reads could fill and block it)."""
    kwargs: dict = {}
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
        if low_priority:
            flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS  # POSIX: the worker lowers its own priority
        kwargs["creationflags"] = flags
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(job.log, "wb") as out:
        return subprocess.Popen([sys.executable, "-m", "paul_vision.label_worker", str(job.path)], cwd=PACKAGE_ROOT,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, **kwargs)


def read_result(job: Job, returncode: int | None) -> dict:
    """The worker's outcome: {"ok": True, ...} or {"ok": False, "error": ..., "spoken": bool}."""
    try:
        data = json.loads(job.result.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("ok"), bool):
            return data
    except (OSError, ValueError):
        pass
    return {"ok": False, "error": f"the label worker ended with code {returncode} and wrote no result"}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0  # deleted since the glob: sorts as the oldest, and its unlink below fails quietly


def clean_up(job: Job) -> None:
    """Delete the job's files except its log, and all but the newest KEEP_LOGS logs."""
    for path in (job.path, job.result, job.frame):
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
    logs = sorted(job.log.parent.glob("*.log"), key=_mtime, reverse=True)
    for old in logs[KEEP_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass


def _quietly(what: str, fn: Callable, *args) -> None:
    """Housekeeping that must not stop the frame loop: an error is logged with its traceback."""
    try:
        fn(*args)
    except Exception:
        log.exception("label change: %s failed", what)


Spawn = Callable[[Job, bool], "subprocess.Popen"]


def run_blocking(cfg: Config, op: str, *, backend: str, keep_export: str | None = None,
                 spawn: Spawn = popen_worker) -> dict:
    """Run the worker for `op` (rebuild or export) in the foreground and commit its result to library.json.
    For startup and the offline commands, never while vision serves frames. RuntimeError if it fails."""
    job = write_job(cfg, op, backend=backend, commit=True, low_priority=False, keep_export=keep_export)
    log.info("label worker: %s for %s (log: %s)", op, backend, job.log)
    proc = spawn(job, False)
    try:
        code = proc.wait(timeout=cfg.labels.worker_timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"the label worker took longer than labels.worker_timeout_s "
                           f"({cfg.labels.worker_timeout_s:g} s); its log is {job.log}") from None
    result = read_result(job, code)
    if not result["ok"]:
        raise RuntimeError(f"the label worker failed: {result['error']} (log: {job.log})")
    clean_up(job)
    return result


def served_export_key(cfg: Config, backend: str, lib: lb.Library) -> str | None:
    """Key of the export in <labels dir>/openvino that serves `lib` on this backend (None: torch, or no tags)."""
    if backend != "openvino" or not lib.tags:
        return None
    return lb.export_key(lib, backend_weights(cfg, backend), cfg.detector.imgsz, cfg.detector.classes)


def prepare_startup(cfg: Config, profile: "Profile", *,
                    spawn: Spawn = popen_worker) -> tuple[lb.Library, lb.ModelViews | None]:
    """The library and what the detector needs for it, before the detector is built. A library made for other weights
    or another input size is rebuilt from its pictures and prompts; on OpenVINO a missing export is built. Both take
    seconds and happen here, before the first frame. ValueError: the library file is broken; RuntimeError: it could
    not be made usable."""
    directory = lb.library_dir(cfg)
    lib = lb.load_library(directory)
    lb.discard_next(directory)  # a change that never went live (vision stopped before it did)
    backend = profile.backend
    if lib.tags:
        stale = lb.stale_reason(lib, profile.weights, cfg.detector.imgsz)
        try:
            if stale:
                log.warning("the label library in %s is rebuilt now: %s", directory, stale)
                run_blocking(cfg, OP_REBUILD, backend=backend, spawn=spawn)
            elif backend == "openvino" and not lb.export_complete(
                    lb.export_dir(directory, served_export_key(cfg, backend, lib))):
                log.warning("building the OpenVINO export for %d tag(s) in %s (about 10 s on the dev laptop, once)",
                            len(lib.tags), directory)
                run_blocking(cfg, OP_EXPORT, backend=backend, spawn=spawn)
        except RuntimeError as exc:
            raise RuntimeError(f"the label library in {directory} could not be made ready: {exc}. {CLI_FIX}, "
                               f"or move {directory} away to start without tags") from exc
        lib = lb.load_library(directory)
    key = served_export_key(cfg, backend, lib)
    lb.prune(directory, [lib], [key] if key else [])
    if not lib.tags:
        return lib, None
    try:
        views = lb.model_views(directory, lib, lb.export_dir(directory, key) if key else None)
    except ValueError as exc:
        raise RuntimeError(f"{exc}; {CLI_FIX}") from exc
    return lib, views


# ---------------------------------------------------------------------------------------------------- live changes

_IDLE, _WORKING, _STOPPING, _LOADING = "idle", "working", "stopping", "loading"


class LabelManager:
    """Takes label commands while vision runs and applies each change between two frames. Frame thread only, except
    the load thread it starts itself. See the module docstring for the steps."""

    def __init__(self, cfg: Config, detector: "UltralyticsDetector", profile: "Profile", library: lb.Library, *,
                 spawn: Spawn = popen_worker, monotonic: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = clock.now):
        self._cfg = cfg
        self._detector = detector
        self._backend = profile.backend
        self._dir = lb.library_dir(cfg)
        self.library = library
        self._spawn = spawn
        self._monotonic = monotonic
        self._now = now
        self._say: Callable[[str], None] = lambda text: None
        self._changed: Callable[[str, bool], None] = lambda name, removed: None
        self._last: tuple[Frame, FrameResult] | None = None
        self._seen: tuple[Frame, FrameResult] | None = None  # the newest frame in which the target was detected
        self._state = _IDLE
        self._request: LabelRequest | None = None
        self._job: Job | None = None
        self._proc = None
        self._started = 0.0      # monotonic: the request was accepted
        self._load_started = 0.0
        self._timings: dict = {}
        self._lock = threading.Lock()
        self._loaded: tuple | None = None  # (new library, PreparedClasses, error), set by the load thread
        self._abandoned = False
        self._thread: threading.Thread | None = None
        self._unsaved: LabelRequest | None = None  # the live change whose library.json could not be written

    # -- wiring ------------------------------------------------------------------------------------------------

    def attach(self, say: Callable[[str], None], changed: Callable[[str, bool], None]) -> None:
        """`say(text)`: speak a sentence once (Pipeline.announce). `changed(name, removed)`: the class list changed,
        called on the frame thread right after the new model is in place (Pipeline.labels_changed)."""
        self._say, self._changed = say, changed

    def observe(self, frame: Frame, result: "FrameResult") -> None:
        """The latest processed frame and what the pipeline made of it (tag_visual takes its box from here)."""
        self._last = (frame, result)
        if result.target is not None and not result.coasting:
            self._seen = (frame, result)

    def known_labels(self) -> list[str]:
        """The labels select_target accepts: detector.classes and the tag names that are live."""
        return lb.known_labels(self._cfg.detector.classes, self.library)

    @property
    def busy(self) -> bool:
        return self._state != _IDLE

    # -- requests ----------------------------------------------------------------------------------------------

    def teach(self, req: LabelRequest) -> bool:
        """Accept or refuse a label command, and say which. True if a change started. Never raises: an unexpected
        error is logged, ends this request's change if it started, and is answered with "Could not learn X."."""
        try:
            return self._teach(req)
        except Exception:
            log.exception("label command %r: unexpected error; it ends as a failure", req)
            self._contain(req)
            return False

    def _teach(self, req: LabelRequest) -> bool:
        name = req.name
        verb = "forget" if req.kind == UNTAG else "learn"
        # The name is checked before any sentence repeats it, so nothing longer than labels.max_name_chars is spoken.
        problem = (lb.name_problem(name, max_chars=self._cfg.labels.max_name_chars, reserved=RESERVED_NAMES)
                   if name else None)
        if problem:
            return self._refuse(req, problem)
        if req.problem:
            return self._refuse(req, req.problem)
        if self.busy:
            current = self._request
            doing = "forgetting" if current.kind == UNTAG else "learning"
            return self._refuse(req, f"Busy {doing} {current.name}. Try again in a moment.")
        if not name:
            return self._refuse(req, "The name is empty.")
        if req.kind == UNTAG:
            if self.library.tag(name) is None:
                return self._refuse(req, f"Nothing was learned as {name}.")
            return self._start(req, OP_REMOVE, f"Forgetting {name}.")

        placeholder = lb.View("-", lb.KIND_TEXT, 0.0, prompt="-")
        try:
            lb.with_view(self.library, name, placeholder, max_tags=self._cfg.labels.max_tags,
                         max_views=self._cfg.labels.max_views)
        except lb.LabelError as exc:
            return self._refuse(req, str(exc))
        if req.kind == TAG_TEXT:
            prompt = req.prompt or name
            if len(prompt) > self._cfg.labels.max_prompt_chars:
                return self._refuse(req, f"Cannot {verb} {name}: the description is too long.")
            return self._start(req, OP_ADD, f"Learning {name}.", view={"kind": lb.KIND_TEXT, "prompt": prompt})
        if req.kind != TAG_VISUAL:
            raise ValueError(f"{req.kind!r} is not a label command")
        picked = self._visual_box(req)
        if isinstance(picked, str):
            return self._refuse(req, picked)
        image, box_px, how = picked
        log.info("tag_visual '%s': box %s px on %dx%d (%s)", name, [round(c) for c in box_px], image.shape[1],
                 image.shape[0], how)
        return self._start(req, OP_ADD, f"Learning {name}.", view={"kind": lb.KIND_VISUAL, "bbox": box_px},
                           frame=image)

    def _visual_box(self, req: LabelRequest) -> tuple[np.ndarray, list[float], str] | str:
        """(picture, box in its pixels, how the box was chosen), or why there is none, as a sentence."""
        cfg = self._cfg.labels
        refuse = f"Cannot learn {req.name}: "
        if self._last is None or self._now() - self._last[0].t > self._cfg.watchdog.stall_timeout_s:
            return refuse + "there is no current picture."
        frame, result = self._last
        if req.bbox is None:
            target = result.target
            if result.message.get("status") != STATUS_OK or target is None:
                return refuse + "nothing is locked."
            if target.source != SOURCE_YOLO:
                return refuse + "the target is a station."
            how = f"the locked target, track {target.track_id} '{target.label}'"
            if result.coasting:
                # Missed in the newest frame only (the selector coasts for selection.coast_s; at 9 FPS on a CPU that
                # is any single miss): learn from the newest frame in which this same target was detected, with its
                # box in that frame, never from a box the detector did not see.
                seen = self._seen
                if (seen is None or seen[1].target is None or seen[1].target.track_id != target.track_id
                        or self._now() - seen[0].t > self._cfg.watchdog.stall_timeout_s):
                    return refuse + "the target is out of view."
                how += f", from its last sighting {(frame.t - seen[0].t) * 1000:.0f} ms before the newest frame"
                frame, result = seen
                target = result.target
            box = target.bbox
        else:
            x1, y1, x2, y2 = req.bbox
            if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
                return refuse + "the box is not valid."
            box, how = (x1, y1, x2, y2), "the box as given"
            best = max(((iou(box, tr.bbox), tr) for tr in result.tracks if tr.source == SOURCE_YOLO),
                       key=lambda pair: pair[0], default=None)
            if best is not None and best[0] >= cfg.snap_iou:
                box = best[1].bbox
                how = f"snapped to track {best[1].track_id} '{best[1].label}' (IoU {best[0]:.2f})"
        x1, y1, x2, y2 = box
        m = cfg.border_margin
        if x1 < m or y1 < m or x2 > 1.0 - m or y2 > 1.0 - m:
            return refuse + "it touches the edge of the picture."
        if x2 - x1 < cfg.min_box_side or y2 - y1 < cfg.min_box_side:
            return refuse + "the box is too small."
        h, w = frame.image.shape[:2]
        return frame.image, [x1 * w, y1 * h, x2 * w, y2 * h], how

    def _refuse(self, req: LabelRequest, sentence: str) -> bool:
        log.warning("label command %s '%s' refused: %s", req.kind, req.name, sentence)
        self._say(sentence)
        return False

    def _start(self, req: LabelRequest, op: str, sentence: str, *, view: dict | None = None,
               frame: np.ndarray | None = None) -> bool:
        if self._unsaved is not None and not self._save():
            # The worker starts from library.json and deletes the files it does not reference: from a library.json
            # without the unsaved change it would drop that change and its files.
            verb = "forget" if req.kind == UNTAG else "learn"
            return self._refuse(req, f"Cannot {verb} {req.name}: the change to {self._unsaved.name} could not be "
                                     f"saved yet.")
        started = self._monotonic()
        job = None
        try:
            job = write_job(self._cfg, op, backend=self._backend, commit=False, low_priority=True,
                            keep_export=served_export_key(self._cfg, self._backend, self.library), name=req.name,
                            view=view, frame=frame)
            proc = self._spawn(job, True)
        except Exception as exc:
            if job is not None:
                _quietly("removing the job files", clean_up, job)
            if not isinstance(exc, OSError):
                raise  # teach() logs it with its traceback and answers
            log.error("label command %s '%s': could not start the label worker: %s", req.kind, req.name, exc)
            self._say(self._failed_sentence(req))
            return False
        self._state, self._request, self._job, self._proc = _WORKING, req, job, proc
        self._started, self._timings = started, {}
        log.info("label command %s '%s' accepted: worker started in %.0f ms (log: %s)", req.kind, req.name,
                 (self._monotonic() - started) * 1000, job.log)
        self._say(sentence)
        return True

    def _failed_sentence(self, req: LabelRequest) -> str:
        try:  # a name that never passed the checks (an error before them) is not spoken
            name = req.name if req.name and not lb.name_problem(
                req.name, max_chars=self._cfg.labels.max_name_chars, reserved=RESERVED_NAMES) else "that"
        except Exception:
            name = "that"
        return f"Could not forget {name}." if req.kind == UNTAG else f"Could not learn {name}."

    def _save(self) -> bool:
        """Try again to make library.next.json library.json, for the change that is live but was not saved."""
        name = self._unsaved.name
        try:
            lb.commit_next(self._dir)
        except Exception as exc:
            log.error("the change to '%s' is live but library.json still cannot be updated: %s: %s", name,
                      type(exc).__name__, exc, exc_info=not isinstance(exc, OSError))
            return False
        self._unsaved = None
        log.info("the change to '%s' is saved in library.json now", name)
        return True

    def _contain(self, req: LabelRequest) -> None:
        """After an unexpected error: end `req`'s change if it is the one in progress, and say that it failed,
        unless that was said already."""
        in_progress = req is not None and req is self._request
        said = in_progress and (self._state == _STOPPING or (self._state == _LOADING and self._abandoned))
        if in_progress:
            try:
                self._abort()
            except Exception:
                log.exception("the label change for '%s' could not be ended cleanly; it is dropped", req.name)
                self._state, self._request, self._job, self._proc, self._thread = _IDLE, None, None, None, None
        if not said:
            try:
                self._say(self._failed_sentence(req))
            except Exception:
                log.exception("the failure of label command %r could not be announced", req)

    def _abort(self) -> None:
        """End the change in progress as a failed one: stop the worker, or let the load thread finish unheard."""
        try:
            running = self._proc is not None and self._proc.poll() is None
        except Exception:
            log.exception("cannot tell whether the label worker still runs")
            running = False
        if running:
            try:
                self._proc.kill()
            except Exception:
                log.exception("the label worker could not be stopped")
            self._state = _STOPPING  # poll() ends the change once the worker is gone: never two workers at once
        elif self._thread is not None and self._thread.is_alive():
            with self._lock:
                self._abandoned = True
            self._state = _LOADING  # poll() ends the change once the thread is done, without a sentence
        else:
            self._end(discard=True)

    # -- progress ----------------------------------------------------------------------------------------------

    def poll(self) -> None:
        """Advance the change in progress. Called on every pass of the main loop; returns at once. Never raises: an
        unexpected error is logged and ends the change in progress as a failed one."""
        req = self._request
        try:
            self._poll()
        except Exception:
            log.exception("label change %r: unexpected error; it ends as a failure", req)
            if req is not None and req is self._request:  # otherwise it ended before the error
                self._contain(req)

    def _poll(self) -> None:
        if self._state == _WORKING:
            self._poll_worker()
        elif self._state == _STOPPING:
            if self._proc.poll() is not None:
                self._end(discard=True)
        elif self._state == _LOADING:
            self._poll_load()

    def _poll_worker(self) -> None:
        req, job = self._request, self._job
        code = self._proc.poll()
        if code is None:
            if self._monotonic() - self._started > self._cfg.labels.worker_timeout_s:
                log.error("label worker for %s '%s' took longer than labels.worker_timeout_s (%g s); stopped it "
                          "(log: %s)", req.kind, req.name, self._cfg.labels.worker_timeout_s, job.log)
                self._proc.kill()
                self._state = _STOPPING  # busy until it is gone, so two workers never write the library at once
                doing = "Forgetting" if req.kind == UNTAG else "Learning"
                self._say(f"{doing} {req.name} took too long.")
            return
        result = read_result(job, code)
        self._timings["worker"] = self._monotonic() - self._started
        if not result["ok"]:
            log.error("label worker for %s '%s' failed: %s (log: %s)", req.kind, req.name, result["error"], job.log)
            self._say(result["error"] if result.get("spoken") else self._failed_sentence(req))
            self._end(discard=True)
            return
        self._timings["worker parts"] = result.get("timings", {})
        self._state, self._load_started, self._abandoned, self._loaded = _LOADING, self._monotonic(), False, None
        self._thread = threading.Thread(target=self._load, args=(result.get("export"),), name="label-load",
                                        daemon=True)
        self._thread.start()

    def _load(self, export: str | None) -> None:
        """Load thread: the staged library into a model that is ready to serve. Never touches the serving model."""
        try:
            new = lb.load_library(self._dir, lb.NEXT_FILE)
            views = lb.model_views(self._dir, new, export if self._backend == "openvino" and new.tags else None)
            out: tuple | None = (new, self._detector.prepare(views), None)
        except Exception as exc:  # reported on the frame thread
            out = (None, None, exc)
        with self._lock:
            if self._abandoned:
                out = None  # too late: drop the model here, off the frame thread
            else:
                self._loaded = out

    def _poll_load(self) -> None:
        req = self._request
        if self._thread.is_alive():
            if not self._abandoned and self._monotonic() - self._load_started > self._cfg.labels.load_timeout_s:
                with self._lock:
                    self._abandoned = True
                log.error("loading the model for %s '%s' took longer than labels.load_timeout_s (%g s); abandoned",
                          req.kind, req.name, self._cfg.labels.load_timeout_s)
                self._say(self._failed_sentence(req))
            return
        with self._lock:
            loaded, self._loaded = self._loaded, None
        if self._abandoned or loaded is None:
            self._end(discard=True)
            return
        new, prepared, error = loaded
        if error is not None:
            log.error("loading the model for %s '%s' failed: %s: %s", req.kind, req.name, type(error).__name__, error)
            self._say(self._failed_sentence(req))
            self._end(discard=True)
            return
        self._timings["load"] = self._monotonic() - self._load_started
        self._apply(req, new, prepared)

    def _apply(self, req: LabelRequest, new: lb.Library, prepared) -> None:
        """Frame thread, between two frames: serve the new class list from the next frame on."""
        removed = req.kind == UNTAG
        swap_started = time.perf_counter()
        retired = [self._detector.adopt(prepared)]
        swap_ms = (time.perf_counter() - swap_started) * 1000
        saved = True
        try:
            lb.commit_next(self._dir)
        except Exception as exc:
            saved = False
            log.error("tag '%s' is live but library.json could not be updated: %s: %s; library.next.json keeps the "
                      "change and the save is tried again before the next change", req.name, type(exc).__name__, exc,
                      exc_info=not isinstance(exc, OSError))
        self.library = new
        self._unsaved = None if saved else req
        self._end(discard=False)  # the change is live: nothing below may report it as failed or discard it
        try:
            self._changed(req.name, removed)
        except Exception:
            log.exception("tag '%s' is live, but the pipeline could not be told", req.name)
        if removed:
            text = (f"Forgot what was taught for {req.name}." if req.name in self._cfg.detector.classes
                    else f"Forgot {req.name}.")
        else:
            text = f"{req.name.capitalize()} is ready."
        if not saved:
            text = text[:-1] + ", but it could not be saved."
        self._say(text)
        total = self._monotonic() - self._started
        log.info("label change %s '%s' live after %.1f s (worker %.1f s %s, load %.1f s, swap %.1f ms); classes %s",
                 req.kind, req.name, total, self._timings.get("worker", 0.0), self._timings.get("worker parts", {}),
                 self._timings.get("load", 0.0), swap_ms, self._detector.labels)
        if retired[0] is not None:
            # The replaced OpenVINO model is freed on a thread of its own: releasing a compiled model can take a while.
            threading.Thread(target=retired.clear, name="label-release", daemon=True).start()

    def _end(self, *, discard: bool) -> None:
        job = self._job
        self._state, self._request, self._job, self._proc, self._thread = _IDLE, None, None, None, None
        if discard:
            _quietly("discarding the staged change", lb.discard_next, self._dir)
        if job is not None:
            _quietly("removing the job files", clean_up, job)

    def close(self) -> None:
        """Stop a worker that still runs; a change that is not live yet is dropped (library.json is unchanged). A
        change that is live but was not saved is saved now if library.json can be replaced."""
        if self._proc is not None and self._proc.poll() is None:
            log.warning("stopping the label worker for '%s': vision is shutting down", self._request.name)
            self._proc.kill()
            try:
                self._proc.wait(timeout=SHUTDOWN_WAIT_S)
            except subprocess.TimeoutExpired:
                log.error("the label worker did not stop")
        if self._state != _IDLE:
            with self._lock:
                self._abandoned = True
            if self._thread is not None and self._thread.is_alive():
                log.warning("vision is shutting down while the model for '%s' loads; it is dropped",
                            self._request.name)
                self._thread.join(timeout=SHUTDOWN_WAIT_S)  # it may still be reading the staged file
            self._end(discard=True)
        if self._unsaved is not None and not self._save():
            log.error("the change to '%s' was never saved: the next start uses library.json as it is",
                      self._unsaved.name)
