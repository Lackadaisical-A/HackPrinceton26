import pytest

from paul_assist import agent, task
from paul_assist.camera import FrameRing
from paul_assist.smart import SmartAssist

JPEG = b"\xff\xd8 picture"
POINTS = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
EVENTS = [{"t": 2.0, "kind": "grasp"}, {"t": 5.0, "kind": "release"}]
BRIEF = {
    "summary": "Moves the white bottle into the left bin.",
    "rule": "White bottles go left.",
    "done_when": "No bottles remain on the table.",
    "objects": [{"name": "white bottle", "description": "small white pill bottle"}],
    "steps": [{"text": "Pick up the bottle", "from": 0, "to": 2, "expect": "bottle held"},
              {"text": "Drop it in the bin", "from": 9, "to": 2, "expect": "bottle in bin"}],
}


# --- Pure logic -------------------------------------------------------------------------------

def test_keyframes_cover_the_ends_the_events_and_the_view_before_a_grasp():
    chosen = task.pick_keyframes(POINTS, EVENTS, limit=5)
    assert {0, 1, 2, 5, 6} <= set(chosen)
    assert task.waypoint_marks(POINTS, EVENTS) == {2: "grasp", 5: "release"}
    assert task.pick_keyframes([], EVENTS) == []
    many = task.pick_keyframes([float(i) for i in range(30)], [], limit=12)
    assert len(many) <= 12 and many[0] == 0 and many[-1] == 29


def test_brief_is_cleaned_and_steps_are_kept_inside_the_recording():
    brief = task.normalize_brief(BRIEF, len(POINTS))
    assert brief["steps"][1] == {"text": "Drop it in the bin", "from": 2, "to": 6, "expect": "bottle in bin"}
    assert brief["objects"][0]["name"] == "white bottle"
    prompt = task.build_brief_prompt(POINTS, EVENTS)
    assert "waypoint 2: 2.0 s (grasp)" in prompt and "7 waypoints" in prompt


@pytest.mark.parametrize("raw", [None, {"summary": ""}, {"summary": "x", "steps": []},
                                 {"summary": "x", "steps": [{"text": "a", "from": "0", "to": 1}]}])
def test_brief_rejects_the_wrong_shape(raw):
    with pytest.raises(ValueError):
        task.normalize_brief(raw, 5)


def test_task_message_shape():
    message = task.build_task_message(3, 10.0, "ok", recording=99.5, brief=BRIEF, model="m")
    assert message["recording"] == 99.5 and message["brief"] is BRIEF
    with pytest.raises(ValueError):
        task.build_task_message(1, 0.0, "ok", recording=1.0)
    with pytest.raises(ValueError):
        task.build_task_message(1, 0.0, "error", recording=1.0, brief=BRIEF)


def test_action_keeps_only_the_fields_its_kind_uses():
    raw = {"action": "nudge", "why": " a  bit left ", "direction": "left", "amount": 2, "waypoint": 4,
           "grip": "close", "step": 1}
    assert agent.normalize_action(raw) == {"action": "nudge", "why": "a bit left", "step": 1,
                                           "direction": "left", "amount": 2}
    assert agent.normalize_action({"action": "move_joints", "why": "", "joints": {"elbow_flex": 5, "gripper": 9,
                                                                                 "wrist_roll": None}})["joints"] == {"elbow_flex": 5}
    assert agent.normalize_action({"action": "done", "why": "all sorted"})["text"] == "all sorted"
    assert agent.normalize_action({"action": "play_segment", "why": "", "waypoint": 1, "to": "3"})["to"] is None
    assert "pan" not in agent.normalize_action({"action": "look", "why": "", "pan": 0})
    for bad in (None, {}, {"action": "fly"}):
        with pytest.raises(ValueError):
            agent.normalize_action(bad)


def test_turn_prompt_carries_the_brief_history_result_and_state():
    history = [agent.history_entry({"action": "grip", "why": "x", "grip": "close"},
                                   {"ok": False, "detail": "Closed on nothing."})]
    prompt = agent.build_turn_prompt(BRIEF, POINTS, EVENTS, history,
                                     {"joints": {"shoulder_pan": 12.5}, "gripper": 80, "holding": False, "closed_endpoint": True},
                                     {"ok": False, "detail": "Closed on nothing."},
                                     {"target": {"label": "bottle"}, "guidance": {"dx": 0.2, "dy": -0.1, "distance_hint": "near"},
                                      "alert": "dropped"}, 4)
    for expected in ('grip {"grip":"close"} -> FAILED: Closed on nothing.', "FAILED. Closed on nothing.",
                     "shoulder_pan 12.5", "holding an object: no", "fully closed on nothing", "locked on 'bottle'",
                     "may have been dropped", "White bottles go left.", "turn 4"):
        assert expected in prompt


def test_agent_message_only_carries_a_seq_with_an_action():
    waiting = agent.build_agent_message(1.0, run="r", seq=3, status="working")
    assert waiting["seq"] is None and waiting["turn"] == 3
    acting = agent.build_agent_message(1.0, run="r", seq=3, status="ok", action={"action": "look", "why": "w"})
    assert (acting["seq"], acting["action"], acting["run"]) == (3, "look", "r")


def test_frame_ring_finds_pictures_by_time():
    clock = [100.0]
    ring = FrameRing("http://unused", clock=lambda: clock[0])
    assert ring.latest() is None and ring.nearest(100.0) is None
    ring.add(98.0, b"a")
    ring.add(99.5, b"b")
    assert ring.nearest(98.4) == b"a" and ring.nearest(90.0) is None
    assert ring.latest() == b"b"
    clock[0] = 105.0
    assert ring.latest() is None
    ring.clear()
    assert ring.nearest(99.5) is None


# --- The service ------------------------------------------------------------------------------

class Frames:
    def __init__(self, have=True):
        self.have, self.on, self.cleared = have, False, 0

    def start(self): self.on = True
    def stop(self): self.on = False
    def clear(self): self.cleared += 1
    def nearest(self, wall, tolerance_s=1.5): return JPEG if self.have else None
    def latest(self, max_age_s=1.5): return JPEG if self.have else None


class Gemini:
    model = "fake"

    def __init__(self):
        self.answers, self.calls = [], []

    def generate(self, parts, system, schema):
        self.calls.append((parts, system))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ready(wall=1000.0):
    return {"phase": "ready", "recorded_wall": wall, "waypoints": POINTS, "events": EVENTS, "agent": None}


def smart_status(seq=1, run="r1", state="waiting", result=None, wall=1000.0):
    return {**ready(wall), "phase": "smart",
            "agent": {"run": run, "state": state, "seq": seq, "result": result,
                      "arm": {"joints": {"shoulder_pan": 1.0}, "gripper": 50, "holding": False, "closed_endpoint": False}}}


def make(gemini=None, frames=None):
    clock = [50.0]
    gemini = gemini if gemini is not None else Gemini()
    frames = frames or Frames()
    service = SmartAssist(gemini, frames, lambda: JPEG, retry_s=3.0, now=lambda: clock[0])
    return service, gemini, frames, clock


def studied():
    service, gemini, frames, clock = make()
    gemini.answers.append(BRIEF)
    service.update(ready())
    service.wait(5)
    return service, gemini, frames, clock


def test_frames_are_collected_only_while_recording_or_driving():
    service, _, frames, _ = make()
    service.update({"phase": "recording"})
    service.update({"phase": "recording"})
    assert frames.on and frames.cleared == 1
    service.update({"phase": "empty"})
    assert not frames.on
    service.update(None)
    assert not frames.on


def test_a_finished_recording_is_studied_once_and_the_brief_published():
    service, gemini, _, clock = make()
    gemini.answers.append(BRIEF)
    first = service.update(ready())
    assert [(topic, m["status"]) for topic, m in first] == [("task", "working")]
    assert service.update(ready()) == []
    done = service.wait(5)
    assert done[0][0] == "task" and done[0][1]["status"] == "ok" and done[0][1]["recording"] == 1000.0
    assert done[0][1]["brief"]["summary"] == BRIEF["summary"]
    parts, system = gemini.calls[0]
    assert system == task.BRIEF_SYSTEM and "Waypoint 2 (grasp):" in parts and parts.count(JPEG) >= 4
    assert service.update(ready()) == []          # the same take is not studied again
    clock[0] += 6
    assert service.update(ready())[0][1]["status"] == "ok"  # but a late console still gets the brief


def test_study_reports_missing_pictures_missing_key_and_gemini_errors():
    service, gemini, _, _ = make(frames=Frames(have=False))
    assert "No camera pictures" in service.update(ready())[0][1]["detail"]
    assert gemini.calls == []
    service = SmartAssist(None, Frames(), lambda: JPEG, unavailable="No key.")
    assert service.update(ready())[0][1]["detail"] == "No key."
    service, gemini, _, _ = make()
    gemini.answers.append(RuntimeError("429 quota"))
    service.update(ready())
    assert "quota" in service.wait(5)[0][1]["detail"]


def test_a_failed_study_is_retried_with_the_same_pictures_then_gives_up():
    service, gemini, _, clock = make()
    gemini.answers += [RuntimeError("503 UNAVAILABLE"), BRIEF]
    service.update(ready())
    assert "Trying again" in service.wait(5)[0][1]["detail"]
    assert [m for m in service.update(ready()) if m[1]["status"] == "working"] == []   # not before the pause is over
    clock[0] += 15.1
    assert any(m["status"] == "working" for _, m in service.update(ready()))
    assert service.wait(5)[0][1]["status"] == "ok"
    service, gemini, _, clock = make()
    gemini.answers += [RuntimeError("503 UNAVAILABLE")] * 5
    for _ in range(5):
        service.update(ready())
        last = service.wait(5)[0][1]
        clock[0] += 15.1
    assert "Record again" in last["detail"]
    assert [m for m in service.update(ready()) if m[1]["status"] == "working"] == []
    assert len(gemini.calls) == 5


def test_each_waiting_turn_gets_one_action_repeated_until_taken():
    service, gemini, _, clock = studied()
    gemini.answers.append({"action": "go_to_waypoint", "waypoint": 1, "why": "over the bottle"})
    assert service.update(smart_status(1))[0][1]["status"] == "working"
    assert service.update(smart_status(1)) == []
    topic, action = service.wait(5)[0]
    assert topic == "agent" and (action["run"], action["seq"], action["action"], action["waypoint"]) == ("r1", 1, "go_to_waypoint", 1)
    parts, system = gemini.calls[-1]
    assert system == agent.AGENT_SYSTEM and parts[-2] == JPEG and "turn 1" in parts[-1]
    assert service.update(smart_status(1)) == []
    clock[0] += 0.6
    assert service.update(smart_status(1)) == [("agent", action)]
    assert service.update(smart_status(1, state="acting")) == []
    assert len(gemini.calls) == 2


def test_results_feed_the_next_turn_and_a_new_run_starts_clean():
    service, gemini, _, _ = studied()
    gemini.answers += [{"action": "grip", "grip": "close", "why": "grab"}, {"action": "look", "why": "check"},
                       {"action": "look", "why": "fresh start"}]
    service.update(smart_status(1))
    service.wait(5)
    service.update(smart_status(2, result={"seq": 1, "ok": False, "detail": "Closed on nothing."}))
    service.wait(5)
    prompt = gemini.calls[-1][0][-1]
    assert 'grip {"grip":"close"} -> FAILED: Closed on nothing.' in prompt
    assert "The camera before your last action:" in gemini.calls[-1][0]
    service.update(smart_status(1, run="r2"))
    service.wait(5)
    assert "What you have done so far" not in gemini.calls[-1][0][-1]


def test_gemini_errors_retry_then_hand_over_to_the_operator():
    service, gemini, _, clock = studied()
    gemini.answers += [RuntimeError("503 UNAVAILABLE")] * 4
    for attempt in range(4):
        assert service.update(smart_status(1))[0][1]["status"] == "working"
        error = service.wait(5)[0][1]
        assert error["status"] == "error" and error["seq"] is None
        assert [m for m in service.update(smart_status(1)) if m[0] == "agent"] == []   # waits before retrying
        clock[0] += 3.1
    action = [m for topic, m in service.update(smart_status(1)) if topic == "agent"][0]
    assert (action["action"], action["seq"]) == ("ask_operator", 1)


def test_a_run_without_a_studied_recording_ends_itself():
    service, gemini, _, _ = make()
    topic, action = service.update(smart_status(1))[0]
    assert action["action"] == "done" and "not studied" in action["text"]
    service, gemini, _, _ = studied()
    assert service.update(smart_status(1, wall=2000.0))[0][1]["action"] == "done"


def test_an_answer_for_a_run_that_ended_is_dropped():
    service, gemini, _, _ = studied()
    gemini.answers.append({"action": "look", "why": "w"})
    service.update(smart_status(1))
    service._run = "another"
    assert service.wait(5) == []
