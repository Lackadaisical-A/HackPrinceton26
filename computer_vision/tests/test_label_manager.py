"""The label manager (M4) with a fake worker process and a fake detector: every state, every answer, the box rules."""
import dataclasses
import json
import logging
import sys
import threading
import time
import types
import zlib
from pathlib import Path

import numpy as np
import pytest

from paul_vision import label_manager as lm
from paul_vision import label_worker as lw
from paul_vision import labels as lb
from paul_vision.app import FrameResult
from paul_vision.commands import LabelRequest
from paul_vision.config import DetectorConfig, LabelsConfig, WatchdogConfig, WeightsConfig
from paul_vision.detector import PreparedClasses, Profile
from paul_vision.types import Frame, Track

CLASSES = ("cup", "bottle")
SIZE = (720, 1280)


@pytest.fixture
def cfg(shipped_cfg, tmp_path):
    labels = LabelsConfig(dir=str(tmp_path / "labels"), max_tags=2, max_views=2, max_name_chars=20,
                          max_prompt_chars=30, snap_iou=0.5, border_margin=0.01, min_box_side=0.04,
                          worker_timeout_s=10.0, worker_threads=2, load_timeout_s=5.0, publish_s=2.0)
    detector = DetectorConfig(backend="auto", device="auto", weights=WeightsConfig(torch="w.pt", openvino="w.pt"),
                              imgsz=[384, 640], classes=list(CLASSES), conf_min=0.15, iou=0.5, max_det=20,
                              agnostic_nms=True, models_dir=str(tmp_path / "models"))
    return dataclasses.replace(shipped_cfg, labels=labels, detector=detector,
                               watchdog=WatchdogConfig(stall_timeout_s=0.5, heartbeat_s=0.5))


class FakeProc:
    def __init__(self):
        self.code = None
        self.killed = False

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


class FakeDetector:
    def __init__(self, events, backend="torch"):
        self.events = events
        self.backend = backend
        self.labels = list(CLASSES)
        self.gate: threading.Event | None = None
        self.fail: Exception | None = None

    def prepare(self, views):
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail is not None:
            raise self.fail
        names = CLASSES + (views.classes if views is not None else ())
        labels = CLASSES + (views.labels if views is not None else ())
        if self.backend == "openvino":
            return PreparedClasses(names, labels, model=f"model for {views.export}", export=views.export)
        return PreparedClasses(names, labels, pe=views.embeddings.copy())

    def adopt(self, prepared):
        self.events.append(("adopt", prepared.names))
        self.labels = list(prepared.labels)
        return "old model" if self.backend == "openvino" else None


class Rig:
    """A LabelManager with fakes around it and a hand-driven clock."""

    def __init__(self, cfg, backend="torch", library=lb.EMPTY):
        self.cfg = cfg
        self.dir = Path(cfg.labels.dir)
        self.events: list = []
        self.said: list[str] = []
        self.procs: list[tuple[lm.Job, FakeProc]] = []
        self.mono = 100.0
        self.wall = 1_000_000.0
        self.detector = FakeDetector(self.events, backend)
        self.spawn_error: OSError | None = None
        self.manager = lm.LabelManager(cfg, self.detector, Profile(backend, "cpu", "w.pt"), library,
                                       spawn=self._spawn, monotonic=lambda: self.mono, now=lambda: self.wall)
        self.manager.attach(say=lambda text: (self.said.append(text), self.events.append(("say", text))),
                            changed=lambda name, removed: self.events.append(("changed", name, removed)))

    def _spawn(self, job, low_priority):
        if self.spawn_error is not None:
            raise self.spawn_error
        assert low_priority
        proc = FakeProc()
        self.procs.append((job, proc))
        return proc

    @property
    def job(self) -> lm.Job:
        return self.procs[-1][0]

    def spec(self) -> dict:
        return json.loads(self.job.path.read_text(encoding="utf-8"))

    # The worker's side, done by hand.
    def worker_succeeds(self, seed=0):
        job, proc = self.procs[-1]
        spec = json.loads(job.path.read_text(encoding="utf-8"))
        lib = lb.load_library(self.dir)
        if spec["op"] == "add":
            v = spec["view"]
            view = (lb.View(f"v{len(self.procs)}", lb.KIND_TEXT, 1.0, prompt=v["prompt"]) if v["kind"] == "text"
                    else lb.View(f"v{len(self.procs)}", lb.KIND_VISUAL, 1.0, image="refs/x.png", bbox=tuple(v["bbox"])))
            new = lb.with_view(lib, spec["name"], view, max_tags=spec["max_tags"], max_views=spec["max_views"])
            ids = [vv.id for _, vv in new.views]
            rows = np.random.default_rng(seed).normal(size=(len(ids), lb.EMBED_DIM)).astype(np.float32)
            new = dataclasses.replace(new, embeddings=lb.save_view_embeddings(self.dir, ids, rows),
                                      weights=spec["weights"], imgsz=tuple(spec["detector"]["imgsz"]))
        else:
            new = lb.subset_embeddings(self.dir, lib, lb.without_tag(lib, spec["name"]))
        lb.write_library(self.dir, new, lb.NEXT_FILE)
        export = None
        if spec["export"] and new.tags:
            export = str(lb.export_dir(self.dir, lb.export_key(new, spec["weights"], spec["detector"]["imgsz"],
                                                               spec["detector"]["classes"])))
        self._finish(proc, {"ok": True, "library": lb.NEXT_FILE, "export": export, "timings": {"embedding": 0.5}}, 0)

    def worker_runs(self):
        """The real worker's job, label_worker.run_job, which first prunes what library.json does not use (the model
        and the text encoder stubbed: see real_worker)."""
        job, proc = self.procs[-1]
        self._finish(proc, lw.run_job(json.loads(job.path.read_text(encoding="utf-8"))), 0)

    def worker_fails(self, result: dict | None, code=1, stage=True):
        job, proc = self.procs[-1]
        if stage:  # a worker that died after staging: the staged file must not survive
            lb.write_library(self.dir, lb.load_library(self.dir), lb.NEXT_FILE)
        self._finish(proc, result, code)

    def _finish(self, proc, result, code):
        job = self.procs[-1][0]
        if result is not None:
            job.result.write_text(json.dumps(result), encoding="utf-8")
        job.log.write_text("worker output\n", encoding="utf-8")
        proc.code = code

    def settle(self, seconds=3.0):
        """Poll until the change is over (the load thread is real)."""
        deadline = time.monotonic() + seconds
        while self.manager.busy and time.monotonic() < deadline:
            self.manager.poll()
            time.sleep(0.005)
        assert not self.manager.busy

    def observe(self, result: FrameResult, age=0.0):
        image = np.full((*SIZE, 3), 7, np.uint8)
        self.manager.observe(Frame(image, self.wall - age, 1), result)


class FakeEncoder:
    """Stands in for label_worker._TextEncoder: a fixed unit vector per prompt."""

    def __init__(self, model, det):
        pass

    def __call__(self, prompt):
        v = np.random.default_rng(zlib.crc32(prompt.encode())).normal(size=lb.EMBED_DIM).astype(np.float32)
        return v / np.linalg.norm(v)


def real_worker(monkeypatch):
    """Lets Rig.worker_runs use label_worker.run_job without loading a model or torch."""
    monkeypatch.setattr(lw, "_load_model", lambda det, weights: object())
    monkeypatch.setattr(lw, "_limit_threads", lambda threads: None)
    monkeypatch.setattr(lw, "_TextEncoder", FakeEncoder)
    monkeypatch.setitem(sys.modules, "torch", sys.modules.get("torch") or types.ModuleType("torch"))


class LockedLibrary:
    """Stands in for lb.commit_next while another process holds library.json: the next `failures` calls fail."""

    def __init__(self, monkeypatch, failures=10**9):
        self.left, self.calls, self._real = failures, 0, lb.commit_next
        monkeypatch.setattr(lb, "commit_next", self)

    def __call__(self, directory):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used by another "
                                      "process")
        self._real(directory)


def boom(*args, **kwargs):
    raise RuntimeError("unexpected")


def text(name="my mug", prompt=None) -> LabelRequest:
    return LabelRequest("tag_text", name, prompt=prompt if prompt is not None else name)


def visual(name="my flask", bbox=None) -> LabelRequest:
    return LabelRequest("tag_visual", name, bbox=bbox)


def untag(name) -> LabelRequest:
    return LabelRequest("untag", name)


def locked(bbox=(0.23, 0.52, 0.30, 0.72), source="yolo", status="ok", coasting=False, others=()) -> FrameResult:
    target = Track(7, "bottle", 0.8, bbox, source) if status == "ok" else None
    return FrameResult({"status": status}, [*([target] if target else []), *others], target, None, coasting)


# --- accept, busy, success ---

def test_tag_text_is_accepted_and_goes_live_only_once_the_model_serves_it(cfg):
    rig = Rig(cfg)
    assert rig.manager.teach(text("my mug", "red mug")) is True
    assert rig.said == ["Learning my mug."] and rig.manager.busy
    spec = rig.spec()
    assert (spec["op"], spec["name"], spec["view"]) == ("add", "my mug", {"kind": "text", "prompt": "red mug"})
    assert spec["commit"] is False and spec["export"] is False and spec["low_priority"] is True
    assert Path(spec["detector"]["models_dir"]).is_absolute()

    rig.manager.poll()
    assert rig.manager.busy and "my mug" not in rig.manager.known_labels()
    rig.worker_succeeds()
    rig.settle()
    assert rig.events[-3:] == [("adopt", ("cup", "bottle", "my mug#0")), ("changed", "my mug", False),
                               ("say", "My mug is ready.")]
    assert rig.manager.known_labels() == ["cup", "bottle", "my mug"]
    assert lb.read_tag_names(rig.dir) == ["my mug"] and not (rig.dir / lb.NEXT_FILE).exists()
    assert not rig.job.path.exists() and not rig.job.result.exists() and rig.job.log.exists()


def test_a_command_while_busy_is_refused_with_a_sentence(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    assert rig.manager.teach(text("my cup")) is False
    assert rig.manager.teach(untag("my mug")) is False
    assert rig.said[1:] == ["Busy learning my mug. Try again in a moment."] * 2
    assert len(rig.procs) == 1


def test_a_second_view_for_the_same_name(cfg):
    rig = Rig(cfg)
    for seed in range(2):
        rig.manager.teach(text("my mug", f"mug {seed}"))
        rig.worker_succeeds(seed)
        rig.settle()
    assert rig.events[-3][1] == ("cup", "bottle", "my mug#0", "my mug#1")
    assert rig.manager.teach(text("my mug", "third")) is False
    assert rig.said[-1] == "My mug already has 2 views."


# --- failures ---

@pytest.mark.parametrize("result, code, sentence", [
    ({"ok": False, "error": "The library is full (2 names).", "spoken": True}, 1, "The library is full (2 names)."),
    ({"ok": False, "error": "RuntimeError: out of memory"}, 1, "Could not learn my mug."),
    (None, 3221225477, "Could not learn my mug."),  # crashed without a result
])
def test_a_failed_worker_is_reported_and_changes_nothing(cfg, result, code, sentence):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.worker_fails(result, code)
    rig.manager.poll()
    assert rig.said[-1] == sentence and not rig.manager.busy
    assert not (rig.dir / lb.NEXT_FILE).exists() and lb.read_tag_names(rig.dir) == []
    assert not any(e[0] == "adopt" for e in rig.events) and rig.manager.known_labels() == list(CLASSES)


def test_a_worker_that_takes_too_long_is_killed_and_reported(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.mono += cfg.labels.worker_timeout_s - 0.1
    rig.manager.poll()
    assert not rig.procs[0][1].killed
    rig.mono += 0.2
    rig.manager.poll()
    assert rig.procs[0][1].killed and rig.said[-1] == "Learning my mug took too long."
    assert rig.manager.busy  # until the process is gone: never two workers on the library
    rig.manager.teach(text("my cup"))
    assert rig.said[-1].startswith("Busy learning my mug")
    rig.worker_fails(None, code=1)
    rig.manager.poll()
    assert not rig.manager.busy and not (rig.dir / lb.NEXT_FILE).exists()
    assert rig.said.count("Learning my mug took too long.") == 1


def test_a_model_that_fails_to_load_is_reported_and_not_adopted(cfg):
    rig = Rig(cfg)
    rig.detector.fail = RuntimeError("the export does not compile")
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    rig.settle()
    assert rig.said[-1] == "Could not learn my mug."
    assert not any(e[0] == "adopt" for e in rig.events) and not (rig.dir / lb.NEXT_FILE).exists()
    assert lb.read_tag_names(rig.dir) == []


def test_a_model_that_loads_too_slowly_is_abandoned(cfg):
    rig = Rig(cfg)
    rig.detector.gate = threading.Event()
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    rig.manager.poll()  # load thread started
    rig.mono += cfg.labels.load_timeout_s + 1
    rig.manager.poll()
    rig.manager.poll()
    assert rig.said[-1] == "Could not learn my mug." and rig.said.count("Could not learn my mug.") == 1
    assert rig.manager.busy  # the thread still runs
    rig.detector.gate.set()
    rig.settle()
    assert not any(e[0] == "adopt" for e in rig.events) and lb.read_tag_names(rig.dir) == []


def test_a_worker_that_cannot_start_is_reported(cfg):
    rig = Rig(cfg)
    rig.spawn_error = OSError("no python")
    assert rig.manager.teach(text("my mug")) is False
    assert rig.said == ["Could not learn my mug."] and not rig.manager.busy


def test_a_change_that_cannot_be_saved_says_so(cfg, monkeypatch):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()

    def locked_file(directory):
        raise PermissionError("in use")

    monkeypatch.setattr(lb, "commit_next", locked_file)
    rig.settle()
    assert rig.said[-1] == "My mug is ready, but it could not be saved."


def test_shutdown_stops_the_worker_and_drops_the_change(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    lb.write_library(rig.dir, lb.EMPTY, lb.NEXT_FILE)
    rig.manager.close()
    assert rig.procs[0][1].killed and not rig.manager.busy
    assert not (rig.dir / lb.NEXT_FILE).exists() and not rig.job.path.exists()
    rig.manager.close()  # twice is fine


def test_shutdown_while_the_model_loads_waits_for_it_and_never_adopts(cfg):
    rig = Rig(cfg)
    rig.detector.gate = threading.Event()
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    rig.manager.poll()  # load thread started, held by the gate
    threading.Timer(0.05, rig.detector.gate.set).start()
    rig.manager.close()  # joins the thread before it deletes the staged file
    assert not rig.manager.busy and not (rig.dir / lb.NEXT_FILE).exists()
    rig.manager.poll()
    assert not any(e[0] == "adopt" for e in rig.events) and lb.read_tag_names(rig.dir) == []
    assert "My mug is ready." not in rig.said


# --- untag ---

def test_untag(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    rig.settle()
    assert rig.manager.teach(untag("my cup")) is False and rig.said[-1] == "Nothing was learned as my cup."
    assert rig.manager.teach(untag("my mug")) is True and rig.said[-1] == "Forgetting my mug."
    assert rig.spec()["op"] == "remove"
    rig.worker_succeeds()
    rig.settle()
    assert rig.events[-3:] == [("adopt", CLASSES), ("changed", "my mug", True), ("say", "Forgot my mug.")]
    assert rig.manager.known_labels() == list(CLASSES) and lb.read_tag_names(rig.dir) == []


def test_untag_of_views_added_to_a_configured_class(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("cup", "tall cup"))
    rig.worker_succeeds()
    rig.settle()
    assert rig.manager.known_labels() == list(CLASSES)
    rig.manager.teach(untag("cup"))
    rig.worker_succeeds()
    rig.settle()
    assert rig.said[-1] == "Forgot what was taught for cup."


# --- refusals before any work ---

@pytest.mark.parametrize("req, sentence", [
    (LabelRequest("tag_text", "", prompt=""), "The name is empty."),
    (text("x" * 21), "That name is too long."),
    (text("my#flask"), "Names cannot contain a slash or a hash sign."),
    (text("drop station"), "That name belongs to a station."),
    (text("my mug", "y" * 31), "Cannot learn my mug: the description is too long."),
    (LabelRequest("tag_visual", "my flask", problem="Cannot learn my flask: the box is not valid."),
     "Cannot learn my flask: the box is not valid."),
])
def test_bad_requests_are_refused_with_a_sentence(cfg, req, sentence):
    rig = Rig(cfg)
    assert rig.manager.teach(req) is False
    assert rig.said == [sentence] and not rig.procs and not rig.manager.busy


def test_a_full_library_is_refused_before_any_work(cfg):
    rig = Rig(cfg)
    for name in ("a", "b"):
        rig.manager.teach(text(name))
        rig.worker_succeeds()
        rig.settle()
    assert rig.manager.teach(text("c")) is False and rig.said[-1] == "The library is full (2 names)."
    assert len(rig.procs) == 2


# --- tag_visual: which box ---

def test_the_null_box_is_the_locked_target(cfg):
    rig = Rig(cfg)
    rig.observe(locked())
    assert rig.manager.teach(visual()) is True
    view = rig.spec()["view"]
    assert view["kind"] == "visual"
    assert view["bbox"] == pytest.approx([0.23 * 1280, 0.52 * 720, 0.30 * 1280, 0.72 * 720])
    picture = np.load(view["frame_npy"])
    assert picture.shape == (*SIZE, 3) and picture[0, 0, 0] == 7
    rig.worker_succeeds()
    rig.settle()
    assert not Path(view["frame_npy"]).exists()


@pytest.mark.parametrize("result, age, sentence", [
    (None, 0.0, "there is no current picture."),
    (locked(), 0.6, "there is no current picture."),
    (locked(status="no_target"), 0.0, "nothing is locked."),
    (locked(status="low_confidence"), 0.0, "nothing is locked."),
    (locked(source="aruco"), 0.0, "the target is a station."),
    (locked(coasting=True), 0.0, "the target is out of view."),
    (locked(bbox=(0.0, 0.5, 0.2, 0.7)), 0.0, "it touches the edge of the picture."),
    (locked(bbox=(0.5, 0.5, 0.52, 0.7)), 0.0, "the box is too small."),
])
def test_the_null_box_needs_a_fresh_locked_object_in_full_view(cfg, result, age, sentence):
    rig = Rig(cfg)
    if result is not None:
        rig.observe(result, age)
    assert rig.manager.teach(visual()) is False
    assert rig.said == [f"Cannot learn my flask: {sentence}"] and not rig.procs


def test_a_target_missed_in_the_newest_frame_is_learned_from_its_last_sighting(cfg):
    rig = Rig(cfg)
    seen_image = np.full((*SIZE, 3), 5, np.uint8)
    rig.manager.observe(Frame(seen_image, rig.wall - 0.1, 1), locked(bbox=(0.23, 0.52, 0.30, 0.72)))
    rig.observe(locked(bbox=(0.4, 0.4, 0.5, 0.6), coasting=True))  # the coasted box, in a picture without the object
    assert rig.manager.teach(visual()) is True
    view = rig.spec()["view"]
    assert view["bbox"] == pytest.approx([0.23 * 1280, 0.52 * 720, 0.30 * 1280, 0.72 * 720])
    assert np.load(view["frame_npy"])[0, 0, 0] == 5  # the picture and the box belong together


@pytest.mark.parametrize("seen_track, seen_age", [(8, 0.1), (7, 0.6)])
def test_a_missed_target_without_its_own_recent_sighting_is_refused(cfg, seen_track, seen_age):
    rig = Rig(cfg)
    other = Track(seen_track, "bottle", 0.8, (0.23, 0.52, 0.30, 0.72), "yolo")
    rig.manager.observe(Frame(np.zeros((*SIZE, 3), np.uint8), rig.wall - seen_age, 1),
                        FrameResult({"status": "ok"}, [other], other, None, False))
    rig.observe(locked(coasting=True))  # track 7
    assert rig.manager.teach(visual()) is False
    assert rig.said == ["Cannot learn my flask: the target is out of view."]


def test_an_explicit_box_snaps_to_the_best_overlapping_object(cfg):
    rig = Rig(cfg)
    flask = Track(3, "bottle", 0.6, (0.23, 0.52, 0.30, 0.72), "yolo")
    other = Track(4, "cup", 0.9, (0.6, 0.2, 0.8, 0.5), "yolo")
    rig.observe(FrameResult({"status": "low_confidence"}, [other, flask], None, None, False))
    assert rig.manager.teach(visual(bbox=(0.22, 0.51, 0.31, 0.71))) is True
    assert rig.spec()["view"]["bbox"] == pytest.approx([0.23 * 1280, 0.52 * 720, 0.30 * 1280, 0.72 * 720])


def test_an_explicit_box_stays_as_given_without_a_close_object(cfg):
    rig = Rig(cfg)
    station = Track(9, "drop station", 0.9, (0.22, 0.51, 0.31, 0.71), "aruco")
    loose = Track(3, "bottle", 0.6, (0.2, 0.4, 0.3, 0.6), "yolo")  # IoU with the box below the snap threshold
    rig.observe(FrameResult({"status": "no_target"}, [station, loose], None, None, False))
    assert rig.manager.teach(visual(bbox=(0.22, 0.51, 0.31, 0.71))) is True
    assert rig.spec()["view"]["bbox"] == pytest.approx([0.22 * 1280, 0.51 * 720, 0.31 * 1280, 0.71 * 720])


@pytest.mark.parametrize("bbox, sentence", [
    ((0.3, 0.5, 0.2, 0.7), "the box is not valid."),
    ((0.2, 0.5, 0.3, 1.2), "the box is not valid."),
    ((0.995, 0.5, 1.0, 0.7), "it touches the edge of the picture."),
    ((0.2, 0.5, 0.3, 0.995), "it touches the edge of the picture."),
    ((0.5, 0.5, 0.51, 0.7), "the box is too small."),
])
def test_bad_explicit_boxes_are_refused(cfg, bbox, sentence):
    rig = Rig(cfg)
    rig.observe(locked(status="no_target"))
    assert rig.manager.teach(visual(bbox=bbox)) is False
    assert rig.said == [f"Cannot learn my flask: {sentence}"]


# --- a change that is live but could not be saved (the real worker, which prunes) ---

def test_the_real_worker_prunes_only_files_no_library_uses(cfg, monkeypatch):
    real_worker(monkeypatch)
    rig = Rig(cfg)
    for req in (text("alpha"), text("beta"), untag("alpha")):
        assert rig.manager.teach(req) is True
        rig.worker_runs()
        rig.settle()
        if req.name == "beta":
            both = rig.manager.library.embeddings
            beta_row = lb.load_view_embeddings(rig.dir, rig.manager.library)[1]
    assert rig.said == ["Learning alpha.", "Alpha is ready.", "Learning beta.", "Beta is ready.", "Forgetting alpha.",
                        "Forgot alpha."]
    lib = lb.load_library(rig.dir)
    assert [t.name for t in lib.tags] == ["beta"] and rig.detector.labels == [*CLASSES, "beta"]
    np.testing.assert_array_equal(lb.load_view_embeddings(rig.dir, lib), [beta_row])
    # The untag's worker started from [alpha, beta]: the alpha-only file went, the two in use stayed.
    assert sorted(p.name for p in rig.dir.glob("views-*.npz")) == sorted([both, lib.embeddings])


def test_a_change_that_could_not_be_saved_is_saved_before_the_next_change_starts(cfg, monkeypatch):
    real_worker(monkeypatch)
    rig = Rig(cfg)
    lock = LockedLibrary(monkeypatch, failures=1)
    rig.manager.teach(text("alpha"))
    rig.worker_runs()
    rig.settle()
    assert rig.said == ["Learning alpha.", "Alpha is ready, but it could not be saved."]
    assert lb.read_tag_names(rig.dir) == [] and (rig.dir / lb.NEXT_FILE).exists()
    alpha_row = lb.load_view_embeddings(rig.dir, rig.manager.library)[0]
    for _ in range(3):
        rig.manager.poll()
    assert lock.calls == 1  # not retried on every pass of the main loop

    assert rig.manager.teach(text("beta")) is True  # saves alpha first, then starts the worker
    assert rig.said[-1] == "Learning beta." and lb.read_tag_names(rig.dir) == ["alpha"]
    rig.worker_runs()
    rig.settle()
    assert rig.said[-1] == "Beta is ready."
    assert lb.read_tag_names(rig.dir) == ["alpha", "beta"] and rig.detector.labels == [*CLASSES, "alpha", "beta"]
    lib = lb.load_library(rig.dir)
    np.testing.assert_array_equal(lb.load_view_embeddings(rig.dir, lib)[0], alpha_row)


def test_while_the_save_keeps_failing_new_changes_are_refused_and_nothing_is_lost(cfg, monkeypatch):
    real_worker(monkeypatch)
    rig = Rig(cfg)
    lock = LockedLibrary(monkeypatch)
    rig.manager.teach(text("alpha"))
    rig.worker_runs()
    rig.settle()
    alpha_npz = rig.manager.library.embeddings
    assert rig.manager.teach(text("beta")) is False
    assert rig.manager.teach(untag("alpha")) is False
    assert rig.said[-2:] == ["Cannot learn beta: the change to alpha could not be saved yet.",
                             "Cannot forget alpha: the change to alpha could not be saved yet."]
    assert len(rig.procs) == 1 and not rig.manager.busy and lock.calls == 3
    assert rig.manager.known_labels() == [*CLASSES, "alpha"] and rig.detector.labels == [*CLASSES, "alpha"]
    staged = lb.load_library(rig.dir, lb.NEXT_FILE)
    assert (rig.dir / alpha_npz).exists() and [t.name for t in staged.tags] == ["alpha"]
    assert not list((rig.dir / lb.JOBS_DIR).glob("*.json"))  # refused before any job was written

    lock.left = 0  # the other process let go of library.json
    assert rig.manager.teach(text("beta")) is True and lb.read_tag_names(rig.dir) == ["alpha"]


@pytest.mark.parametrize("failures, on_disk", [(1, ["my mug"]), (2, [])])
def test_shutdown_tries_once_more_to_save(cfg, monkeypatch, failures, on_disk):
    rig = Rig(cfg)
    LockedLibrary(monkeypatch, failures)
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    rig.settle()
    rig.manager.close()
    assert lb.read_tag_names(rig.dir) == on_disk
    assert (rig.dir / lb.NEXT_FILE).exists() == (not on_disk)  # still unsaved: the next start discards it


# --- unexpected errors on the frame thread ---

@pytest.mark.parametrize("worker_ok, sentence", [(True, "My mug is ready."), (False, "Could not learn my mug.")])
def test_an_error_while_cleaning_up_is_logged_and_the_next_command_is_accepted(cfg, monkeypatch, caplog,
                                                                              worker_ok, sentence):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    monkeypatch.setattr(lm, "clean_up", boom)
    rig.worker_succeeds() if worker_ok else rig.worker_fails(None)
    with caplog.at_level(logging.ERROR):
        rig.settle()
        rig.manager.poll()
    assert rig.said == ["Learning my mug.", sentence] and not rig.manager.busy
    assert lb.read_tag_names(rig.dir) == (["my mug"] if worker_ok else []) and not (rig.dir / lb.NEXT_FILE).exists()
    assert any(r.exc_info for r in caplog.records)  # with its traceback
    assert rig.manager.teach(text("my cup")) is True and rig.said[-1] == "Learning my cup."


def test_an_error_while_adopting_the_model_ends_the_change_as_failed(cfg, monkeypatch, caplog):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.worker_succeeds()
    monkeypatch.setattr(rig.detector, "adopt", boom)
    with caplog.at_level(logging.ERROR):
        rig.settle()
    assert rig.said == ["Learning my mug.", "Could not learn my mug."]
    assert lb.read_tag_names(rig.dir) == [] and not (rig.dir / lb.NEXT_FILE).exists()
    assert rig.manager.known_labels() == list(CLASSES) and any(r.exc_info for r in caplog.records)
    assert rig.manager.teach(text("my cup")) is True


def test_an_error_after_the_worker_started_stops_it(cfg, monkeypatch):
    rig = Rig(cfg)
    said = []

    def say(text):  # the answer "Learning my mug." cannot be delivered
        said.append(text)
        if text == "Learning my mug.":
            raise RuntimeError("unexpected")

    rig.manager.attach(say=say, changed=lambda name, removed: None)
    assert rig.manager.teach(text("my mug")) is False
    assert said == ["Learning my mug.", "Could not learn my mug."] and rig.procs[0][1].killed
    assert rig.manager.busy  # until the killed worker is gone: never two workers on the library
    rig.worker_fails(None)
    rig.manager.poll()
    assert not rig.manager.busy and said[-1] == "Could not learn my mug."


@pytest.mark.parametrize("error", [OSError("no python"), RuntimeError("bad handle")])
def test_a_worker_that_cannot_start_leaves_no_job_files(cfg, error):
    rig = Rig(cfg)
    rig.observe(locked())
    rig.spawn_error = error
    assert rig.manager.teach(visual()) is False  # a visual job: a .json and the picture as .npy
    assert rig.said == ["Could not learn my flask."] and not rig.manager.busy
    jobs = rig.dir / lb.JOBS_DIR
    assert jobs.is_dir() and not [p.name for p in jobs.iterdir() if p.suffix in (".json", ".npy")]
    rig.spawn_error = None
    assert rig.manager.teach(visual()) is True and rig.said[-1] == "Learning my flask."


def test_keyboard_interrupt_is_not_swallowed(cfg):
    rig = Rig(cfg)
    rig.spawn_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        rig.manager.teach(text("my mug"))


# --- a name from a command is checked before any sentence repeats it ---

LONG = "x" * 200


@pytest.mark.parametrize("req, sentence", [
    (text(LONG), "That name is too long."),
    (visual(LONG), "That name is too long."),
    (untag(LONG), "That name is too long."),
    (LabelRequest("tag_visual", LONG, problem=f"Cannot learn {LONG}: the box is not valid."),
     "That name is too long."),
    (LabelRequest("tag_text", LONG, problem=f"Cannot learn {LONG}: the description is empty."),
     "That name is too long."),
    (untag("drop station"), "That name belongs to a station."),
    (untag("my/flask"), "Names cannot contain a slash or a hash sign."),
])
def test_a_name_that_fails_the_name_check_is_refused_with_that_check(cfg, req, sentence):
    rig = Rig(cfg)
    rig.observe(locked())
    assert rig.manager.teach(req) is False
    assert rig.said == [sentence] and not rig.procs


def test_a_long_name_is_not_repeated_while_busy_either(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug"))
    rig.manager.teach(untag(LONG))
    assert rig.said == ["Learning my mug.", "That name is too long."]


def test_an_error_before_the_name_is_checked_does_not_speak_the_name(cfg, monkeypatch):
    rig = Rig(cfg)
    monkeypatch.setattr(lb, "name_problem", boom)
    assert rig.manager.teach(text(LONG)) is False and rig.manager.teach(untag(LONG)) is False
    assert rig.said == ["Could not learn that.", "Could not forget that."]


# --- OpenVINO: the export, and the old model let go of ---

def test_openvino_builds_an_export_and_keeps_the_served_one(cfg):
    rig = Rig(cfg, backend="openvino")
    rig.manager.teach(text("my mug"))
    spec = rig.spec()
    assert spec["export"] is True and spec["keep_export"] is None
    rig.worker_succeeds()
    rig.settle()
    assert rig.said[-1] == "My mug is ready."
    served = lm.served_export_key(cfg, "openvino", rig.manager.library)
    assert served is not None
    rig.manager.teach(text("my cup"))
    assert rig.spec()["keep_export"] == served  # the worker's clean-up must not delete the model that serves


# --- startup ---

class BlockingWorker:
    """Stands in for popen_worker in run_blocking: does the job when waited on."""

    def __init__(self, cfg, outcome="ok"):
        self.cfg, self.outcome, self.ops = cfg, outcome, []

    def __call__(self, job, low_priority):
        assert low_priority is False
        spec = json.loads(job.path.read_text(encoding="utf-8"))
        self.ops.append(spec["op"])
        outer = self

        class Proc:
            def wait(self, timeout=None):
                d = Path(spec["labels_dir"])
                if outer.outcome != "ok":
                    job.result.write_text(json.dumps({"ok": False, "error": "RuntimeError: picture missing"}))
                    return 1
                lib = lb.load_library(d)
                new = dataclasses.replace(lib, weights=spec["weights"], imgsz=tuple(spec["detector"]["imgsz"]))
                if spec["export"]:
                    out = lb.export_dir(d, lb.export_key(new, spec["weights"], spec["detector"]["imgsz"],
                                                         spec["detector"]["classes"]))
                    out.mkdir(parents=True, exist_ok=True)
                    for name in ("metadata.yaml", "m.xml", "m.bin"):
                        (out / name).write_text("x")
                lb.write_library(d, new)
                job.result.write_text(json.dumps({"ok": True, "export": None, "timings": {}}))
                return 0

            def kill(self):
                pass

        return Proc()


def stored(cfg, weights="w.pt", imgsz=(384, 640)) -> lb.Library:
    """A library of one text tag on disk, made for `weights` at `imgsz`."""
    d = Path(cfg.labels.dir)
    d.mkdir(parents=True, exist_ok=True)
    npz = lb.save_view_embeddings(d, ["v1"], np.ones((1, lb.EMBED_DIM), np.float32))
    lib = lb.Library(weights, imgsz, npz, (lb.Tag("my flask", (lb.View("v1", lb.KIND_TEXT, 1.0, prompt="flask"),)),))
    lb.write_library(d, lib)
    return lib


def test_startup_without_a_library(cfg):
    worker = BlockingWorker(cfg)
    assert lm.prepare_startup(cfg, Profile("openvino", "xpu", "w.pt"), spawn=worker) == (lb.EMPTY, None)
    assert worker.ops == []


def test_startup_on_torch_uses_the_library_as_it_is(cfg):
    lib = stored(cfg)
    lb.write_library(Path(cfg.labels.dir), lib, lb.NEXT_FILE)  # left by a change that never went live
    worker = BlockingWorker(cfg)
    got, views = lm.prepare_startup(cfg, Profile("torch", "cpu", "w.pt"), spawn=worker)
    assert got == lib and views.classes == ("my flask#0",) and views.labels == ("my flask",) and views.export is None
    assert worker.ops == [] and not (Path(cfg.labels.dir) / lb.NEXT_FILE).exists()


def test_startup_on_openvino_builds_a_missing_export_once(cfg):
    stored(cfg)
    worker = BlockingWorker(cfg)
    _, views = lm.prepare_startup(cfg, Profile("openvino", "xpu", "w.pt"), spawn=worker)
    assert worker.ops == ["export"] and lb.export_complete(Path(views.export))
    lm.prepare_startup(cfg, Profile("openvino", "xpu", "w.pt"), spawn=worker)
    assert worker.ops == ["export"]


def test_startup_rebuilds_a_library_made_for_other_weights(cfg):
    stored(cfg, weights="old.pt")
    worker = BlockingWorker(cfg)
    lib, views = lm.prepare_startup(cfg, Profile("torch", "cpu", "w.pt"), spawn=worker)
    assert worker.ops == ["rebuild"] and lib.weights == "w.pt"


def test_startup_stops_with_the_command_that_fixes_a_failed_rebuild(cfg):
    stored(cfg, imgsz=(480, 640))
    with pytest.raises(RuntimeError, match=r"paul_vision\.labels rebuild.*labels remove"):
        lm.prepare_startup(cfg, Profile("torch", "cpu", "w.pt"), spawn=BlockingWorker(cfg, outcome="fail"))


# --- several descriptions in one change (tag_text with `prompts`; ../assist sends Gemini's) ---

def many(name="my mug", *prompts) -> LabelRequest:
    return LabelRequest("tag_text", name, prompts=tuple(prompts))


def test_several_descriptions_are_added_in_one_change_by_the_real_worker(cfg, monkeypatch):
    real_worker(monkeypatch)
    rig = Rig(dataclasses.replace(cfg, labels=dataclasses.replace(cfg.labels, max_views=4)))
    rig.manager.teach(text("my mug", "red mug"))
    rig.worker_runs()
    rig.settle()
    first_row = lb.load_view_embeddings(rig.dir, rig.manager.library)[0]

    assert rig.manager.teach(many("my mug", "mug from above", "Red Mug", "mug handle side", "mug from above")) is True
    assert rig.said[-1] == "Learning more about my mug."
    spec = rig.spec()
    assert "view" not in spec and spec["views"] == [{"kind": "text", "prompt": "mug from above"},
                                                    {"kind": "text", "prompt": "mug handle side"}]  # no repeats
    rig.worker_runs()
    rig.settle()
    assert rig.said[-1] == "My mug is ready." and len(rig.procs) == 2
    tag = lb.load_library(rig.dir).tag("my mug")
    assert [v.prompt for v in tag.views] == ["red mug", "mug from above", "mug handle side"]
    rows = lb.load_view_embeddings(rig.dir, rig.manager.library)
    assert rows.shape == (3, lb.EMBED_DIM)
    np.testing.assert_array_equal(rows[0], first_row)  # the view that was there keeps its embedding
    assert rig.detector.labels == [*CLASSES, "my mug", "my mug", "my mug"]


def test_descriptions_are_cut_to_the_views_that_are_free_and_too_long_ones_are_left_out(cfg):
    rig = Rig(cfg)
    free = cfg.labels.max_views
    prompts = [f"mug {i}" for i in range(free + 2)]
    assert rig.manager.teach(many("my mug", "x" * (cfg.labels.max_prompt_chars + 1), *prompts)) is True
    assert [v["prompt"] for v in rig.spec()["views"]] == prompts[:free]


def test_descriptions_that_add_nothing_are_refused(cfg):
    rig = Rig(cfg)
    rig.manager.teach(text("my mug", "red mug"))
    rig.worker_succeeds()
    rig.settle()
    assert rig.manager.teach(many("my mug", "RED MUG", "x" * (cfg.labels.max_prompt_chars + 1))) is False
    assert rig.said[-1] == "Cannot learn my mug: there is no new description to add."
    assert len(rig.procs) == 1
