import io
import json

import pytest

from paul_assist.analysis import (MAX_ITEMS, MAX_PROMPT_CHARS, build_message, build_prompt, match_known,
                                  normalize_name, normalize_result)
from paul_assist.camera import read_first_jpeg
from paul_assist.known import known_labels
from paul_assist.service import Assistant, _short_reason

KNOWN = ["cup", "bottle", "pill bottle", "box", "my flask"]

ANSWER = {
    "scene": "A person in bed with a bottle and a cup on the table.",
    "patient": {"present": True, "observations": ["Person reaching toward the table"], "urgency": "watch"},
    "suggestions": [{"text": "Hand the patient the water bottle", "item": "Bottles"},
                    {"text": "Check on the patient", "item": None}],
    "items": [
        {"name": "Bottles", "prompt": "clear plastic water bottle", "reason": "thirst", "visible": True},
        {"name": "Inhaler!", "prompt": "blue asthma inhaler", "reason": "breathing", "visible": False},
    ],
}


def test_normalize_marks_known_items_and_links_suggestions():
    result = normalize_result(ANSWER, KNOWN)
    assert [i["name"] for i in result["items"]] == ["bottle", "inhaler"]
    assert [i["known"] for i in result["items"]] == [True, False]
    assert result["suggestions"][0]["item"] == "bottle"
    assert result["suggestions"][1]["item"] is None
    assert result["patient"]["urgency"] == "watch"


def test_normalize_caps_and_cleans():
    raw = dict(ANSWER, items=[{"name": f"thing {i}", "prompt": "x" * 500, "reason": "", "visible": True}
                              for i in range(6)])
    raw["patient"] = {"present": "yes", "observations": [], "urgency": "panic"}
    result = normalize_result(raw, KNOWN)
    assert len(result["items"]) == MAX_ITEMS
    assert all(len(i["prompt"]) == MAX_PROMPT_CHARS for i in result["items"])
    assert result["patient"] == {"present": False, "observations": [], "urgency": "none", "state": "not_visible"}


@pytest.mark.parametrize("raw", [None, [], {"scene": ""}])
def test_normalize_rejects_the_wrong_shape(raw):
    with pytest.raises(ValueError):
        normalize_result(raw, KNOWN)


def test_names():
    assert normalize_name("  Pill-Bottle!! ") == "pill bottle"
    assert match_known("cups", KNOWN) == "cup"
    assert match_known("glasses", KNOWN) is None


def test_prompt_carries_known_names_and_target():
    prompt = build_prompt(KNOWN, {"status": "ok", "phase": "approach", "target": {"label": "cup"}})
    assert "my flask" in prompt and "locked on: cup" in prompt


def test_message_requires_result_only_when_ok():
    assert build_message(1, 1.0, "ok", result={})["result"] == {}
    with pytest.raises(ValueError):
        build_message(1, 1.0, "ok")
    with pytest.raises(ValueError):
        build_message(1, 1.0, "error", result={})


def test_read_first_jpeg_from_multipart():
    jpeg = b"\xff\xd8 picture \xff\xd9"
    body = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg) + jpeg + b"\r\n--frame\r\n"
    assert read_first_jpeg(io.BytesIO(body)) == jpeg
    with pytest.raises(ValueError):
        read_first_jpeg(io.BytesIO(b"--frame\r\n"))


def test_known_labels_reads_classes_and_tags(tmp_path):
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "library.json").write_text(json.dumps({"tags": [{"name": "my flask"}, {"name": "cup"}]}))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("detector:\n  classes: [cup, bottle]\nlabels:\n  dir: labels\n")
    assert known_labels(cfg) == ["cup", "bottle", "my flask"]
    assert known_labels(tmp_path / "missing.yaml") == []


class FakeAnalyzer:
    model = "fake"

    def __init__(self, answer=ANSWER, error=None):
        self.answer, self.error, self.calls = answer, error, 0

    def analyze(self, image, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def make(analyzer, clock, grab=lambda: b"\xff\xd8"):
    return Assistant(analyzer, grab, lambda: KNOWN, cooldown_s=3.0, now=lambda: clock[0])


def test_request_publishes_working_then_ok():
    clock = [100.0]
    assistant = make(FakeAnalyzer(), clock)
    [working] = assistant.request(None)
    assert working["status"] == "working"
    [done] = assistant.wait(5)
    assert done["status"] == "ok" and done["seq"] == working["seq"] + 1
    assert done["result"]["items"][1]["name"] == "inhaler"


def test_cooldown_and_busy_ignore_requests():
    clock = [100.0]
    analyzer = FakeAnalyzer()
    assistant = make(analyzer, clock)
    assistant.request(None)
    assistant.wait(5)
    clock[0] = 101.0
    assert assistant.request(None) == []
    clock[0] = 104.0
    assert assistant.request(None)[0]["status"] == "working"
    assistant.wait(5)
    assert analyzer.calls == 2


def test_failures_become_error_messages():
    clock = [0.0]
    assistant = make(FakeAnalyzer(error=RuntimeError("429 RESOURCE_EXHAUSTED")), clock)
    assistant.request(None)
    [done] = assistant.wait(5)
    assert done["status"] == "error" and "quota" in done["detail"]

    def no_camera():
        raise OSError("refused")

    assistant = make(FakeAnalyzer(), clock, grab=no_camera)
    assistant.request(None)
    assert "camera" in assistant.wait(5)[0]["detail"]


def test_missing_key_is_an_error_not_a_crash():
    assistant = Assistant(None, lambda: b"", lambda: [], unavailable="No GEMINI_API_KEY.")
    [message] = assistant.request(None)
    assert message == {**message, "status": "error", "detail": "No GEMINI_API_KEY."}


def test_short_reason():
    assert "API key" in _short_reason(RuntimeError("400 API key not valid"))
    assert "in time" in _short_reason(TimeoutError("timed out"))


def test_overloaded_is_reported_plainly():
    assert "overloaded" in _short_reason(RuntimeError("503 UNAVAILABLE high demand"))


def test_falls_back_to_the_next_model(monkeypatch):
    from paul_assist import gemini

    analyzer = gemini.GeminiAnalyzer.__new__(gemini.GeminiAnalyzer)
    analyzer.model = "a"
    analyzer._models = ["a", "b", "c"]
    tried = []

    def ask(model, *request):
        tried.append(model)
        if model == "a":
            raise RuntimeError("503 UNAVAILABLE")
        return {"ok": model}

    analyzer._ask = ask
    assert analyzer.analyze(b"", "") == {"ok": "b"}
    assert tried == ["a", "b"] and analyzer.model == "b"

    def refuse(model, *request):
        raise RuntimeError("400 API key not valid")

    analyzer._ask = refuse
    with pytest.raises(RuntimeError):
        analyzer.analyze(b"", "")


def test_eyes_closed_always_gets_the_wellness_nudge_first_and_is_not_no_concern():
    raw = dict(ANSWER, patient={"present": True, "observations": ["Lying still"], "urgency": "none",
                                "state": "eyes_closed"},
               touch_checks=[{"kind": "weight_check", "text": "Lift the cup to feel if it is empty", "reason": ""}])
    result = normalize_result(raw, KNOWN)
    assert [c["kind"] for c in result["touch_checks"]] == ["wellness_nudge", "weight_check"]
    assert result["touch_checks"][0]["text"] == "Nudge the patient gently to check for wellness"
    assert result["patient"]["urgency"] == "watch"


def test_touch_checks_are_cleaned():
    raw = dict(ANSWER, patient={"present": False, "observations": [], "urgency": "none", "state": "awake"},
               touch_checks=[{"kind": "attention_tap", "text": "Tap", "reason": ""},
                             {"kind": "punch", "text": "no", "reason": ""},
                             {"kind": "weight_check", "text": "Lift the bottle", "reason": "may be empty"},
                             {"kind": "weight_check", "text": "again", "reason": ""}])
    result = normalize_result(raw, KNOWN)
    assert result["patient"]["state"] == "not_visible"
    assert result["touch_checks"] == [{"kind": "weight_check", "text": "Lift the bottle", "reason": "may be empty"}]


def test_prompt_follows_up_a_recent_nudge_only():
    previous = {"patient": {"state": "eyes_closed", "urgency": "watch"}, "touch_checks": [{"kind": "wellness_nudge"}]}
    assert "wellness_nudge" in build_prompt(KNOWN, None, previous, 40)
    assert "Previous" not in build_prompt(KNOWN, None, previous, 600)


def test_second_analysis_carries_the_first_into_the_prompt():
    clock = [100.0]

    class Recording(FakeAnalyzer):
        prompts: list = []

        def analyze(self, image, prompt):
            self.prompts.append(prompt)
            return super().analyze(image, prompt)

    analyzer = Recording()
    assistant = make(analyzer, clock)
    assistant.request(None)
    assistant.wait(5)
    clock[0] = 130.0
    assistant.request(None)
    assistant.wait(5)
    assert "Previous" not in analyzer.prompts[0] and "Previous analysis 30 s ago" in analyzer.prompts[1]
