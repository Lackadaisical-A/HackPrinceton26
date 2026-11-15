"""Tags reaching the pipeline (M4): the tracker forgets its votes, a narrowed target follows its new name, nothing
says "Target lost.", and run() wires the label manager to the commands and the summary."""
import dataclasses
import json
import logging
import time
from pathlib import Path

import numpy as np
import pytest

from paul_vision import label_manager as lm
from paul_vision import labels as lb
from paul_vision.app import Pipeline, run
from paul_vision.detector import PreparedClasses, Profile
from paul_vision.summary import TEXT_TARGET_LOST
from paul_vision.types import Frame

from conftest import det

IMAGE = np.zeros((72, 128, 3), np.uint8)


class Scene:
    """The flask under one box: called `bottle` until the tag is live, then `my flask` (as the model would)."""

    description = "fake"

    def __init__(self, before="bottle", after="my flask", conf_after=0.6):
        self.before, self.after, self.conf_after = before, after, conf_after
        self.tagged = False
        self.labels = ["cup", "bottle"]

    def detect(self, image):
        if self.tagged:
            return [det(self.after, self.conf_after, cx=0.4, cy=0.5, w=0.15, h=0.3)]
        return [det(self.before, 0.9, cx=0.4, cy=0.5, w=0.15, h=0.3)]

    # The label manager's side of UltralyticsDetector.
    def prepare(self, views):
        extra = views.classes if views is not None else ()
        return PreparedClasses(("cup", "bottle", *extra), ("cup", "bottle", *(views.labels if views else ())),
                               pe=np.zeros((1, 2 + len(extra), 512), np.float32))

    def adopt(self, prepared):
        self.tagged = len(prepared.names) > 2
        self.labels = list(prepared.labels)
        return None


def frames(pipeline, start, count, t0=100.0):
    return [pipeline.process(Frame(IMAGE, t0 + (start + i) / 30, start + i)) for i in range(count)]


def summaries(results):
    return [r.message["summary"] for r in results if r.message["summary"]]


# --- Pipeline ---

def test_a_tag_going_live_renames_the_locked_track_at_once_and_keeps_the_lock(shipped_cfg):
    scene = Scene()
    p = Pipeline(shipped_cfg, scene)
    before = frames(p, 1, 60)
    assert before[-1].target.label == "bottle"
    track = before[-1].target.track_id
    scene.tagged = True          # the swap, between two frames
    p.labels_changed("my flask", removed=False)
    p.announce("My flask is ready.")
    after = frames(p, 61, 90)
    assert after[0].message["summary"] == "My flask is ready."
    # The new label wins on the first frame, though "bottle" had a long, confident vote (cleared by forget_labels).
    assert all(r.message["status"] == "ok" and r.target.track_id == track and r.target.label == "my flask"
               for r in after)
    assert TEXT_TARGET_LOST not in summaries(before + after)
    assert any(s.startswith("My flask") for s in summaries(after)[1:])


def test_without_forgetting_the_votes_the_old_label_would_hold(shipped_cfg):
    # The reason for forget_labels: the same scene without labels_changed keeps calling the flask a bottle.
    scene = Scene()
    p = Pipeline(shipped_cfg, scene)
    frames(p, 1, 60)
    scene.tagged = True
    assert frames(p, 61, 1)[0].target.label == "bottle"


def test_a_target_narrowed_to_its_old_label_follows_the_new_name(shipped_cfg):
    scene = Scene()
    p = Pipeline(shipped_cfg, scene)
    p.set_preferred_label("bottle")       # select_target bottle, then the bottle is taught as "my flask"
    before = frames(p, 1, 60)
    track = before[-1].target.track_id
    scene.tagged = True
    p.labels_changed("my flask", removed=False)
    after = frames(p, 61, 90)
    assert all(r.message["status"] == "ok" and r.target.track_id == track and r.target.label == "my flask"
               for r in after)
    assert TEXT_TARGET_LOST not in summaries(after)
    assert p._preferred == "my flask"  # the narrowing moved with it: another bottle would not be taken now


def test_the_narrowing_does_not_follow_if_the_target_changed_meanwhile(shipped_cfg):
    scene = Scene()
    p = Pipeline(shipped_cfg, scene)
    p.set_preferred_label("bottle")
    frames(p, 1, 30)
    p.labels_changed("my flask", removed=False)
    p.set_preferred_label("cup")      # the operator chose something else before the next frame
    frames(p, 31, 5)
    assert p._preferred == "cup"


def test_removing_the_tag_the_target_was_narrowed_to_lifts_the_narrowing(shipped_cfg):
    scene = Scene(before="my flask", after="bottle", conf_after=0.9)
    p = Pipeline(shipped_cfg, scene)
    p.set_preferred_label("my flask")
    frames(p, 1, 30)
    scene.tagged = True              # the tag is gone: the model calls the flask a bottle again
    p.labels_changed("my flask", removed=True)
    after = frames(p, 31, 30)
    assert after[-1].message["status"] == "ok" and after[-1].target.label == "bottle"


def test_removing_views_of_a_configured_class_keeps_the_narrowing(shipped_cfg):
    p = Pipeline(shipped_cfg, Scene(before="cup", after="cup"))
    p.set_preferred_label("cup")
    p.labels_changed("cup", removed=True)
    assert frames(p, 1, 30)[-1].target.label == "cup"


# --- announcements: spoken with the next frame, unless the feed stopped for longer than the stall timeout ---

class Wall:
    """The clock the frames are stamped with (clock.now() in vision), set by hand."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_answers_queued_during_a_long_stall_are_dropped_not_spoken_late(shipped_cfg, caplog):
    wall = Wall()
    p = Pipeline(shipped_cfg, Scene(), now=wall)
    frames(p, 0, 30)                    # t = 100.0 to 100.97
    wall.t = 101.0                      # the camera stalls; meanwhile the operator tries twice
    late = ["Cannot learn my flask: there is no current picture.", "Learning my mug.", "My mug is ready."]
    for sentence in late:
        p.announce(sentence)
    p.reset()                           # e.g. a camera switch
    with caplog.at_level(logging.INFO, logger="paul_vision.app"):
        first = p.process(Frame(IMAGE, 400.0, 31))  # five minutes later
    said = summaries([first, *frames(p, 1, 60, t0=400.0)])
    assert not any(sentence in s for s in said for sentence in late)
    assert all(f"announcement dropped, 299.0 s old when the next frame came: {sentence!r}" in caplog.text
               for sentence in late)


def test_an_answer_given_just_before_the_feed_resumes_is_spoken(shipped_cfg):
    wall = Wall()
    p = Pipeline(shipped_cfg, Scene(), now=wall)
    frames(p, 0, 30)
    wall.t = 101.0
    p.announce("Learning my mug.")      # stale by the next frame
    wall.t = 400.0 - shipped_cfg.watchdog.stall_timeout_s  # still current by the next frame, just
    p.announce("Cannot learn my flask: there is no current picture.")
    p.reset()
    assert p.process(Frame(IMAGE, 400.0, 31)).message["summary"] == ("Cannot learn my flask: there is no current "
                                                                      "picture.")


def test_answers_are_spoken_with_the_next_frame_while_frames_flow(shipped_cfg):
    wall = Wall()
    p = Pipeline(shipped_cfg, Scene(), now=wall)
    results = []
    for i in range(1, 91):
        t = 100.0 + i / 30
        wall.t = t - 1 / 60             # announced between two frames, as the label manager does
        if i in (30, 60):
            p.announce(f"Answer {i}.")
            p.announce(f"Answer {i}b.")
        results.append(p.process(Frame(IMAGE, t, i)))
    assert results[29].message["summary"] == "Answer 30. Answer 30b."
    assert results[59].message["summary"] == "Answer 60. Answer 60b."
    assert [s for s in summaries(results) if s.startswith("Answer")] == ["Answer 30. Answer 30b.",
                                                                        "Answer 60. Answer 60b."]


# --- run(): commands to the label manager and back, with a fake worker ---

class InstantWorker:
    """Spawn for LabelManager: does the worker's job at once (embeddings are made up), like the real one would."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job, low_priority):
        spec = json.loads(job.path.read_text(encoding="utf-8"))
        self.jobs.append(spec)
        d = Path(spec["labels_dir"])
        lib = lb.load_library(d)
        if spec["op"] == lm.OP_ADD:
            v = spec["view"]
            assert v["kind"] == "visual" and np.load(v["frame_npy"]).shape == IMAGE.shape
            view = lb.View(lb.new_view_id(), lb.KIND_VISUAL, 1.0, image="refs/x.png", bbox=tuple(v["bbox"]))
            new = lb.with_view(lib, spec["name"], view, max_tags=spec["max_tags"], max_views=spec["max_views"])
            ids = [x.id for _, x in new.views]
            new = dataclasses.replace(new, weights=spec["weights"], imgsz=tuple(spec["detector"]["imgsz"]),
                                      embeddings=lb.save_view_embeddings(d, ids, np.ones((len(ids), 512), np.float32)))
        else:
            new = lb.subset_embeddings(d, lib, lb.without_tag(lib, spec["name"]))
        lb.write_library(d, new, lb.NEXT_FILE)
        job.result.write_text(json.dumps({"ok": True, "export": None, "timings": {}}), encoding="utf-8")

        class Done:
            def poll(self):
                return 0

        return Done()


class Script:
    """A command feed: payloads become due once a given number of frames has been processed."""

    def __init__(self, at: dict[int, dict]):
        self.at, self.frames, self.sent = dict(at), 0, []

    def seen(self, frame, result):
        self.frames += 1

    def poll(self):
        due = [n for n in self.at if n <= self.frames]
        out = [self.at.pop(n) for n in sorted(due)]
        self.sent += out
        return out


class FrameList:
    """Frames as fast as run() takes them; from frame `hold_from` on, none until `ready()` (a real camera would keep
    delivering while the model loads on its thread; here that thread would otherwise lose the race to the clip's end).
    """

    description = "fake"

    def __init__(self, count, hold_from=None, ready=lambda: True, wait_s=10.0):
        self.seq, self.count, self.ended = 0, count, False
        self.hold_from, self.ready, self.wait_s, self.deadline = hold_from, ready, wait_s, None
        self.t = 1_000.0

    def read(self, timeout_s):
        if self.seq >= self.count:
            self.ended = True
            return None
        if self.hold_from is not None and self.seq >= self.hold_from and not self.ready():
            self.deadline = self.deadline or time.monotonic() + self.wait_s
            if time.monotonic() < self.deadline:
                time.sleep(min(timeout_s, 0.005))
                return None  # run() polls the commands and the label manager meanwhile
        self.seq += 1
        self.t += 1 / 30
        return Frame(IMAGE, self.t, self.seq)

    def close(self):
        pass


class Collect:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def test_run_teaches_the_locked_object_and_answers_out_loud(shipped_cfg, tmp_path, caplog):
    cfg = dataclasses.replace(shipped_cfg, labels=dataclasses.replace(shipped_cfg.labels, dir=str(tmp_path)))
    scene, worker = Scene(), InstantWorker()
    clock_now = {"t": 0.0}
    manager = lm.LabelManager(cfg, scene, Profile("torch", "cpu", cfg.detector.weights.torch), lb.EMPTY,
                              spawn=worker, now=lambda: clock_now["t"])
    script = Script({10: {"type": "select_target", "label": "bottle"},
                     20: {"t": 5.0, "type": "tag_visual", "name": "My Flask", "bbox": None},
                     22: {"type": "tag_text", "name": "other"},             # busy: refused with a sentence
                     60: {"type": "select_target", "label": "my flask"}})
    source, out = FrameList(90, hold_from=30, ready=lambda: not manager.busy), Collect()

    def seen(frame, result):
        clock_now["t"] = frame.t  # the manager judges the picture's age on the frames' own clock
        script.seen(frame, result)

    with caplog.at_level(logging.INFO):
        run(cfg, source, scene, out, on_result=seen, commands=script, labels=manager)
    manager.close()

    said = [m["summary"] for m in out.messages if m["summary"]]
    assert said.count("Learning my flask.") == 1 and said.count("My flask is ready.") == 1
    assert "Busy learning my flask. Try again in a moment." in said
    assert TEXT_TARGET_LOST not in said
    assert len(worker.jobs) == 1 and worker.jobs[0]["name"] == "my flask"
    ready = next(i for i, m in enumerate(out.messages) if m["summary"] == "My flask is ready.")
    locked = [m for m in out.messages[10:] if m["status"] == "ok"]
    assert len(locked) == len(out.messages[10:])                      # never lost the lock
    assert {m["target"]["track_id"] for m in locked} == {locked[0]["target"]["track_id"]}
    assert all(m["target"]["label"] == "bottle" for m in out.messages[10:ready])  # not renamed before it is ready
    assert all(m["target"]["label"] == "my flask" for m in out.messages[ready:])
    assert lb.read_tag_names(tmp_path) == ["my flask"] and manager.known_labels()[-1] == "my flask"
    assert "only 'my flask' can be the target now" in caplog.text     # select_target took the live tag name
    assert not manager.busy
