import json
import struct

import pytest

from paul_assist import enrich
from paul_assist.enrich import (ENRICH_INSTRUCTION, ENRICH_SCHEMA, Enricher, box_1000, build_enrich_prompt, load_state,
                                normalize_prompts, png_size, read_library)

PNG = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1280, 720) + b"\x08\x02\x00\x00\x00"


def library(tmp_path, tags, max_views=5):
    labels = tmp_path / "labels"
    (labels / "refs").mkdir(parents=True, exist_ok=True)
    (labels / "refs" / "a1.png").write_bytes(PNG)
    (tmp_path / "config.yaml").write_text(
        f"detector:\n  classes: [cup]\nlabels:\n  dir: labels\n  max_views: {max_views}\n  max_prompt_chars: 40\n",
        encoding="utf-8")
    (labels / "library.json").write_text(json.dumps({"version": 1, "tags": tags}), encoding="utf-8")
    return tmp_path / "config.yaml"


def snapshot(view_id="a1"):
    return {"id": view_id, "kind": "visual", "added": 1.0, "image": f"refs/{view_id}.png",
            "bbox": [320.0, 180.0, 640.0, 360.0]}


def described(view_id, prompt):
    return {"id": view_id, "kind": "text", "added": 1.0, "prompt": prompt}


VASELINE = {"name": "vaseline", "views": [snapshot()]}


# --- the pure parts ---

def test_png_size_reads_the_header():
    assert png_size(PNG) == (1280, 720)
    with pytest.raises(ValueError):
        png_size(b"\xff\xd8 not a png")


def test_the_box_is_given_as_gemini_reads_boxes():
    assert box_1000((320.0, 180.0, 640.0, 360.0), (1280, 720)) == [250, 250, 500, 500]  # ymin, xmin, ymax, xmax
    assert box_1000((-5.0, 0.0, 2000.0, 720.0), (1280, 720)) == [0, 0, 1000, 1000]


def test_the_prompt_names_the_object_its_box_and_what_is_already_known():
    prompt = build_enrich_prompt("vaseline", ("blue tub",), 3, [250, 250, 500, 500])
    assert '"vaseline"' in prompt and "[250, 250, 500, 500]" in prompt and '"blue tub"' in prompt
    assert "Write 3 phrases" in prompt and "other sides" in prompt and "dim or harsh light" in prompt
    assert "no picture" in build_enrich_prompt("my mug", (), 2, None)
    assert "other sides" in ENRICH_INSTRUCTION or "another side" in ENRICH_INSTRUCTION
    assert ENRICH_SCHEMA["required"] == ["object", "prompts"]


def test_normalize_keeps_only_new_short_phrases():
    raw = {"object": "tub", "prompts": ["  White tub,   blue lid. ", "white tub, blue lid", "BLUE TUB", 7, "",
                                        "x" * 41, "round lid seen from above", "tub lying on its side", "extra one"]}
    assert normalize_prompts(raw, ("blue tub",), 3, 40) == ["White tub, blue lid", "round lid seen from above",
                                                            "tub lying on its side"]


@pytest.mark.parametrize("raw", [None, [], {"prompts": "white tub"}, {"object": "tub"}])
def test_normalize_gives_nothing_for_another_shape(raw):
    assert normalize_prompts(raw, (), 3, 40) == []


def test_read_library_gives_each_name_its_snapshot_and_descriptions(tmp_path):
    config = library(tmp_path, [VASELINE, {"name": "my mug", "views": [described("t1", "red mug")]}], max_views=4)
    info = read_library(config)
    vaseline, mug = info.tags
    assert (info.max_views, info.max_prompt_chars, info.directory) == (4, 40, tmp_path / "labels")
    assert (vaseline.name, vaseline.views, vaseline.key) == ("vaseline", 1, "vaseline|a1")
    assert vaseline.image == tmp_path / "labels" / "refs" / "a1.png" and vaseline.bbox == (320.0, 180.0, 640.0, 360.0)
    assert (mug.prompts, mug.image, mug.bbox) == (("red mug",), None, None)


def test_read_library_without_a_library_or_a_config(tmp_path):
    config = library(tmp_path, [])
    (tmp_path / "labels" / "library.json").unlink()
    assert read_library(config).tags == ()
    (tmp_path / "labels" / "library.json").write_text("{ not json", encoding="utf-8")
    assert read_library(config).tags == ()
    assert read_library(tmp_path / "missing.yaml") is None


# --- the enricher ---

class FakeGemini:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def generate(self, parts, system, schema):
        self.calls.append((parts, system, schema))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Rig:
    def __init__(self, tmp_path, tags, gemini, **kwargs):
        self.config = library(tmp_path, tags, **{k: v for k, v in kwargs.items() if k == "max_views"})
        self.dir = tmp_path / "labels"
        self.t = 0.0
        self.sent = []
        self.listening = True
        self.gemini = gemini
        self.enricher = Enricher(gemini, lambda: read_library(self.config), self._send, now=lambda: self.t,
                                 count=kwargs.get("count", 3))

    def _send(self, command):
        if self.listening:
            self.sent.append(command)
        return self.listening

    def labels(self, tags, busy=None):
        self.enricher.saw_labels({"t": 1.0, "classes": ["cup"], "busy": busy,
                                  "tags": [{"name": n, "views": v} for n, v in tags]})

    def step(self, seconds=enrich.SCAN_S):
        """Time passes, the loop polls, and a running Gemini call finishes."""
        self.t += seconds
        self.enricher.poll()
        self.enricher.wait(5)

    def state(self):
        return load_state(self.dir / enrich.STATE_FILE)


ANSWER = {"object": "tub", "prompts": ["white tub with blue lid", "round blue lid from above", "tub lying on side"]}


def test_a_taught_object_gets_descriptions_once_vision_is_free(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.labels([("vaseline", 1)], busy="vaseline")  # vision is still making the snapshot live
    rig.step()
    assert rig.gemini.calls == [] and rig.sent == []
    rig.labels([("vaseline", 1)])
    rig.step()
    parts, system, schema = rig.gemini.calls[0]
    assert parts[0] == PNG and "[250, 250, 500, 500]" in parts[1] and '"vaseline"' in parts[1]
    assert (system, schema) == (ENRICH_INSTRUCTION, ENRICH_SCHEMA)
    command = rig.sent[0]
    assert (command["type"], command["name"], command["prompts"]) == ("tag_text", "vaseline", ANSWER["prompts"])
    assert rig.state() == {}  # not done until vision shows the new views

    rig.labels([("vaseline", 1)], busy="vaseline")
    rig.step()
    rig.labels([("vaseline", 4)])
    rig.step()
    assert rig.state() == {"vaseline|a1": "done"} and not rig.enricher.busy
    for _ in range(5):
        rig.step()
    assert len(rig.gemini.calls) == 1 and len(rig.sent) == 1  # once, and never again


def test_descriptions_wait_for_vision_to_listen_and_to_be_idle(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.step()  # no `labels` message yet: Gemini is asked, nothing is sent
    assert len(rig.gemini.calls) == 1 and rig.sent == []
    rig.labels([("vaseline", 1)], busy="airpods")
    rig.step()
    assert rig.sent == []
    rig.labels([("vaseline", 1)])
    rig.listening = False
    rig.step()
    assert rig.sent == []
    rig.listening = True
    rig.step()
    assert len(rig.sent) == 1 and len(rig.gemini.calls) == 1  # the same descriptions, not asked for again


def test_an_old_labels_message_does_not_count_as_idle(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.labels([("vaseline", 1)])
    rig.step()
    assert len(rig.sent) == 1
    rig2 = Rig(tmp_path / "b", [VASELINE], FakeGemini(ANSWER))
    rig2.labels([("vaseline", 1)])
    rig2.t += enrich.LABELS_FRESH_S + 1  # vision stopped
    rig2.step()
    assert rig2.sent == []


def test_a_done_name_is_remembered_across_restarts_but_a_retaught_one_is_new(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.labels([("vaseline", 1)])
    rig.step()
    rig.labels([("vaseline", 4)])
    rig.step()
    again = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    again.labels([("vaseline", 4)])
    again.step()
    assert again.gemini.calls == []
    (tmp_path / "labels" / "refs" / "b2.png").write_bytes(PNG)
    retaught = Rig(tmp_path, [{"name": "vaseline", "views": [snapshot("b2")]}], FakeGemini(ANSWER))
    retaught.labels([("vaseline", 1)])
    retaught.step()
    assert len(retaught.gemini.calls) == 1


def test_gemini_failing_is_tried_again_then_given_up(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(RuntimeError("503 UNAVAILABLE")))
    rig.labels([("vaseline", 1)])
    for _ in range(6):
        rig.step()
    assert len(rig.gemini.calls) == enrich.MAX_ATTEMPTS and rig.sent == []
    assert rig.state() == {"vaseline|a1": "failed"}


def test_an_answer_with_nothing_usable_counts_as_a_failure(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini({"object": "tub", "prompts": ["x" * 80]}))
    rig.labels([("vaseline", 1)])
    for _ in range(6):
        rig.step()
    assert rig.sent == [] and rig.state() == {"vaseline|a1": "failed"}


def test_descriptions_vision_never_added_are_sent_once_more_then_given_up(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.labels([("vaseline", 1)])
    rig.step()
    for _ in range(2):
        rig.labels([("vaseline", 1)])
        rig.step(enrich.CONFIRM_S + 1)   # still one view: the wait is over
        rig.labels([("vaseline", 1)])
        rig.step()
    assert len(rig.sent) == enrich.MAX_ATTEMPTS and len(rig.gemini.calls) == 1
    assert rig.state() == {"vaseline|a1": "failed"}


def test_only_the_free_views_are_asked_for_and_a_full_name_is_left_alone(tmp_path):
    full = {"name": "airpods", "views": [snapshot(), described("t1", "white case"), described("t2", "open case")]}
    rig = Rig(tmp_path, [full, {"name": "vaseline", "views": [snapshot(), described("t3", "blue tub")]}],
              FakeGemini(ANSWER), max_views=3)
    rig.labels([("airpods", 3), ("vaseline", 2)])
    rig.step()
    assert rig.state() == {"airpods|a1": "done"}
    assert "Write 1 phrases" in rig.gemini.calls[0][0][1] and '"blue tub"' in rig.gemini.calls[0][0][1]
    assert rig.sent[0]["name"] == "vaseline" and rig.sent[0]["prompts"] == ANSWER["prompts"][:1]


def test_a_name_taught_by_description_is_described_without_a_picture(tmp_path):
    rig = Rig(tmp_path, [{"name": "my mug", "views": [described("t1", "red mug")]}], FakeGemini(ANSWER))
    rig.labels([("my mug", 1)])
    rig.step()
    parts = rig.gemini.calls[0][0]
    assert len(parts) == 1 and "no picture" in parts[0] and '"red mug"' in parts[0]
    assert rig.sent[0]["name"] == "my mug"


def test_a_name_forgotten_meanwhile_drops_its_descriptions(tmp_path):
    rig = Rig(tmp_path, [VASELINE], FakeGemini(ANSWER))
    rig.labels([("vaseline", 1)])
    rig.step()
    (tmp_path / "labels" / "library.json").write_text(json.dumps({"version": 1, "tags": []}), encoding="utf-8")
    rig.labels([])
    rig.step()
    assert not rig.enricher.busy and rig.state() == {}


def test_without_gemini_nothing_happens(tmp_path):
    rig = Rig(tmp_path, [VASELINE], None)
    rig.labels([("vaseline", 1)])
    rig.step()
    assert rig.sent == [] and not rig.enricher.busy


# --- the command publisher, on real ZMQ ---

def test_commands_are_only_sent_while_vision_listens():
    import time

    import zmq

    from paul_assist.__main__ import CommandSender

    ctx = zmq.Context()
    try:
        sender = CommandSender(ctx, "tcp://127.0.0.1:*")
        command = {"t": 1.0, "type": "tag_text", "name": "vaseline", "prompts": ["white tub with blue lid"]}
        assert sender.send(command) is False  # nobody listens: not sent, not queued

        vision = ctx.socket(zmq.SUB)  # as paul_vision.transport.Subscriber subscribes
        vision.setsockopt(zmq.LINGER, 0)
        vision.connect(sender.endpoint)
        vision.setsockopt(zmq.SUBSCRIBE, b"command ")
        deadline = time.monotonic() + 5
        while not sender.send(command):
            assert time.monotonic() < deadline, "the subscription never arrived"
            time.sleep(0.01)
        assert vision.poll(2000)
        topic, _, body = vision.recv_string().partition(" ")
        assert topic == "command" and json.loads(body) == command

        vision.close()
        deadline = time.monotonic() + 5
        while sender.send(command):  # vision went away: the unsubscribe is seen
            assert time.monotonic() < deadline, "the unsubscribe never arrived"
            time.sleep(0.01)
    finally:
        ctx.destroy(linger=0)
