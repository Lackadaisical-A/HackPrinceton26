"""The whole pipeline with a fake camera and a fake detector: no hardware, real ZMQ sockets."""
import dataclasses
import threading
import time

import cv2
import numpy as np
import pytest

from paul_vision import clock
from paul_vision.__main__ import main as vision_main
from paul_vision.app import Pipeline, Recorder, run
from paul_vision.config import GripperLoadConfig, WatchdogConfig
from paul_vision.gripper_load import grip_level_of, reading_from_robot
from paul_vision.messages import validate_vision
from paul_vision.pressure import Contact, ContactDetector, PressureReading
from paul_vision.subscribe_print import default_endpoints, format_vision, main as subscribe_main, quiet_notice
from paul_vision.summary import TEXT_DEGRADED
from paul_vision.transport import Subscriber, VisionPublisher
from paul_vision.types import Frame

from conftest import det

ANY_PORT = "tcp://127.0.0.1:0"
FAST = WatchdogConfig(stall_timeout_s=0.15, heartbeat_s=0.1)
IMAGE = np.zeros((72, 128, 3), np.uint8)
CUP_LEFT = [det("cup", 0.9, cx=0.3, cy=0.5)]


class FakeDetector:
    """Returns a scripted answer per call; the last entry repeats. An exception in the script is raised."""

    description = "fake"

    def __init__(self, *script):
        self._script = list(script)
        self.calls = 0

    def detect(self, image):
        answer = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


class FakeSource:
    """Plays a script: a number is a frame after that many seconds, ("stall", s) is s seconds without frames."""

    description = "fake"

    def __init__(self, script):
        self._script = list(script)
        self._seq = 0
        self._stall_until = None
        self.ended = False

    def read(self, timeout_s):
        if not self._script:
            self.ended = True
            return None
        step = self._script[0]
        if isinstance(step, tuple):
            if self._stall_until is None:
                self._stall_until = time.monotonic() + step[1]
            if time.monotonic() >= self._stall_until:
                self._script.pop(0)
                self._stall_until = None
            else:
                time.sleep(min(timeout_s, 0.02))
            return None
        self._script.pop(0)
        time.sleep(step)
        self._seq += 1
        return Frame(IMAGE, clock.now(), self._seq)

    def close(self):
        pass


@pytest.fixture
def cfg(shipped_cfg):
    return dataclasses.replace(shipped_cfg, watchdog=FAST)


def frame(seq: int, t: float) -> Frame:
    return Frame(IMAGE, t, seq)


def run_and_collect(cfg, source, detector) -> list[dict]:
    """Run the loop to the end of the source and return every message a subscriber received from it."""
    with VisionPublisher(ANY_PORT, send_hwm=200, watchdog=cfg.watchdog) as pub, Subscriber(pub.endpoint) as sub:
        deadline = time.monotonic() + 5
        while not sub.recv(0.05):  # the publisher's own heartbeats show when the subscription is live
            assert time.monotonic() < deadline, "subscriber never connected"
        run(cfg, source, detector, pub)
        finished = clock.now()
        received = []
        while (item := sub.recv(0.1)) is not None:
            received.append(item[1])
    for msg in received:
        validate_vision(msg)
    # The publisher outlives the loop here, so its watchdog also reports the silence after the last frame.
    received = [m for m in received if m["t"] <= finished]
    # Drop what was sent before the first frame (heartbeats from the connection wait).
    first = next(i for i, m in enumerate(received) if m["frame_id"] >= 1 and m["status"] != "degraded")
    return received[first:]


def runs_of(statuses):
    out = []
    for status in statuses:
        if not out or out[-1] != status:
            out.append(status)
    return out


# --- Pipeline: frame in, message out ---

def test_switching_camera_clears_previous_scene_before_processing_new_frame(cfg):
    class SwitchingSource(FakeSource):
        generation = 0

        def read(self, timeout_s):
            result = super().read(timeout_s)
            if self._seq == 2:
                self.generation = 1
            return result

    messages = []
    class Publisher:
        def publish(self, message):
            messages.append(message)

    run(cfg, SwitchingSource([0, 0]), FakeDetector(CUP_LEFT, []), Publisher())
    assert messages[0]["status"] == "ok"
    assert messages[1]["status"] == "no_target"
    assert messages[1]["phase"] == "search"
    assert messages[1]["target"] is None and messages[1]["guidance"] is None

def test_no_detections_gives_no_target(cfg):
    result = Pipeline(cfg, FakeDetector([])).process(frame(1, 100.0))
    msg = result.message
    validate_vision(msg)
    assert (msg["status"], msg["phase"], msg["target"], msg["guidance"]) == ("no_target", "search", None, None)
    assert (msg["t"], msg["frame_id"]) == (100.0, 1)


def test_weak_detections_give_low_confidence(cfg):
    weak = (cfg.detector.conf_min + cfg.selection.conf_lock) / 2
    msg = Pipeline(cfg, FakeDetector([det("cup", weak)])).process(frame(1, 100.0)).message
    validate_vision(msg)
    assert (msg["status"], msg["target"], msg["guidance"]) == ("low_confidence", None, None)


def test_a_confident_detection_becomes_the_target_with_guidance_towards_it(cfg):
    pipeline = Pipeline(cfg, FakeDetector([det("cup", 0.9, cx=0.3, cy=0.7)]))
    for i in range(30):
        msg = pipeline.process(frame(i + 1, 100.0 + i / 30)).message
        validate_vision(msg)
        assert msg["status"] == "ok" and msg["phase"] == "approach"
    assert msg["target"]["label"] == "cup" and msg["target"]["source"] == "yolo"
    assert msg["target"]["bbox"] == pytest.approx([0.2, 0.6, 0.4, 0.8])
    assert msg["guidance"]["dx"] == pytest.approx(-0.4, abs=0.02)  # target is left of centre
    assert msg["guidance"]["dy"] == pytest.approx(0.4, abs=0.02)   # and below it
    assert msg["guidance"]["aligned"] is False


def test_a_missed_detection_is_coasted_over_then_the_target_is_dropped(cfg):
    assert cfg.selection.coast_s > 0
    pipeline = Pipeline(cfg, FakeDetector(CUP_LEFT, CUP_LEFT, []))
    pipeline.process(frame(1, 100.00))
    locked = pipeline.process(frame(2, 100.03))
    coasting = pipeline.process(frame(3, 100.06))
    assert coasting.message["status"] == "ok" and coasting.coasting
    assert coasting.message["guidance"] == locked.message["guidance"]  # held, not extrapolated
    dropped = pipeline.process(frame(4, 100.06 + cfg.selection.coast_s + 0.05))
    assert dropped.message["status"] == "no_target" and dropped.message["guidance"] is None


def test_the_summary_is_spoken_once_not_every_frame(cfg):
    pipeline = Pipeline(cfg, FakeDetector(CUP_LEFT))
    summaries = [pipeline.process(frame(i + 1, 100.0 + i / 30)).message["summary"] for i in range(45)]
    spoken = [s for s in summaries if s]
    assert len(spoken) == 1 and "cup" in spoken[0].lower() and "left" in spoken[0]


def test_tracking_state_is_reset_after_a_gap_in_the_feed(cfg):
    pipeline = Pipeline(cfg, FakeDetector(CUP_LEFT, CUP_LEFT, [det("cup", 0.9, cx=0.8, cy=0.5)]))
    pipeline.process(frame(1, 100.00))
    pipeline.process(frame(2, 100.03))
    after = pipeline.process(frame(3, 105.0)).message  # the object is somewhere else entirely
    assert after["status"] == "ok"
    assert after["guidance"]["dx"] == pytest.approx(0.6, abs=0.01)  # not smoothed from the old position


def test_preferred_label_restricts_the_target(cfg):
    both = [det("bottle", 0.9, cx=0.5, cy=0.5), det("cup", 0.6, cx=0.2, cy=0.5)]
    pipeline = Pipeline(cfg, FakeDetector(both))
    assert pipeline.process(frame(1, 100.0)).message["target"]["label"] == "bottle"
    pipeline = Pipeline(cfg, FakeDetector(both))
    pipeline.set_preferred_label("cup")
    assert pipeline.process(frame(1, 100.0)).message["target"]["label"] == "cup"


# --- the loop, over real sockets ---

def test_every_frame_is_published_as_a_valid_message(cfg):
    received = run_and_collect(cfg, FakeSource([0.005] * 20), FakeDetector(CUP_LEFT))
    assert [m["frame_id"] for m in received] == list(range(1, 21))
    assert all(m["status"] == "ok" and m["target"]["label"] == "cup" and m["guidance"]["dx"] < 0 for m in received)
    assert all(0 <= time.time() - m["t"] < 10 for m in received)  # t is a real Unix time


def test_a_stalled_feed_is_reported_as_degraded_and_recovers(cfg):
    source = FakeSource([0.005] * 5 + [("stall", 0.5)] + [0.005] * 5)
    received = run_and_collect(cfg, source, FakeDetector(CUP_LEFT))
    assert runs_of(m["status"] for m in received) == ["ok", "degraded", "ok"]
    degraded = [m for m in received if m["status"] == "degraded"]
    assert len(degraded) >= 2
    assert degraded[0]["summary"] == TEXT_DEGRADED and degraded[0]["frame_id"] == 5
    assert all(m["target"] is None and m["guidance"] is None for m in degraded)
    assert [m["frame_id"] for m in received if m["status"] == "ok"] == list(range(1, 11))


def test_a_failing_detector_looks_like_a_stalled_feed_not_like_no_target(cfg):
    detector = FakeDetector(CUP_LEFT, CUP_LEFT, CUP_LEFT, RuntimeError("inference failed"))
    received = run_and_collect(cfg, FakeSource([0.01] * 40), detector)
    assert runs_of(m["status"] for m in received) == ["ok", "degraded"]
    assert detector.calls == 40  # it keeps trying, so it can recover


def test_max_frames_stops_the_loop(cfg):
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=cfg.watchdog) as pub:
        seen = []
        metrics = run(cfg, FakeSource([0.001] * 50), FakeDetector([]), pub, max_frames=7,
                      on_result=lambda frame, result: seen.append(frame.seq))
    assert metrics.frames == 7 and seen == list(range(1, 8))
    assert metrics.statuses == {"no_target": 7}
    assert "7 frames" in metrics.report() and "detect" in metrics.report()


class ScriptedCommands:
    """Stands in for CommandReceiver: each poll hands out the next batch, then nothing."""

    def __init__(self, *batches):
        self._batches = list(batches)

    def poll(self):
        return self._batches.pop(0) if self._batches else []


def test_commands_change_the_target_while_the_loop_runs(cfg):
    both = [det("bottle", 0.9, cx=0.5, cy=0.5), det("cup", 0.6, cx=0.2, cy=0.5)]
    commands = ScriptedCommands(
        [], [], [],
        [{"type": "select_target", "label": "cup"}],       # before frame 4
        [], [],
        [{"type": "select_target", "label": "unicorn"}],   # not a class: ignored, the cup stays
        [], [],
        [{"type": "set_haptics", "enabled": False}, {"type": "select_target", "label": "bottle"}],  # before frame 10
    )
    labels = []
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=cfg.watchdog) as pub:
        run(cfg, FakeSource([0.001] * 12), FakeDetector(both), pub, commands=commands,
            on_result=lambda frame, result: labels.append((result.message["target"] or {}).get("label")))
    assert labels == ["bottle"] * 3 + ["cup"] * 6 + ["bottle"] * 3


def test_the_camera_stream_shows_the_frames_the_loop_processed(cfg):
    from paul_vision.config import StreamConfig
    from paul_vision.stream import FrameStream
    from test_stream import Viewer, wait_for

    stream_cfg = StreamConfig(enabled=True, host="127.0.0.1", port=0, max_width=960, jpeg_quality=80, max_fps=500)
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=cfg.watchdog) as pub, \
            FrameStream(stream_cfg) as stream, Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        run(cfg, FakeSource([0.01] * 10), FakeDetector(CUP_LEFT), pub,
            on_result=lambda frame, result: stream.submit(frame.image))
        picture = viewer.picture()
    assert picture.shape == IMAGE.shape


# --- recorder ---

def frames_in(path) -> int:
    cap = cv2.VideoCapture(str(path))
    count = 0
    while cap.read()[0]:
        count += 1
    cap.release()
    return count


def test_recorder_repeats_frames_so_the_clip_plays_in_real_time(tmp_path):
    path = tmp_path / "clip.avi"
    recorder = Recorder(path, fps=30)
    for i, t in enumerate([50.0, 50.1, 50.2]):  # a 10 FPS pipeline
        recorder.write(frame(i + 1, t + 0.001))
    recorder.close()
    assert frames_in(path) == 7  # 0.2 s of footage at 30 fps, plus the last frame


def test_recorder_does_not_fill_a_long_stall(tmp_path):
    path = tmp_path / "clip.avi"
    recorder = Recorder(path, fps=30)
    recorder.write(frame(1, 50.001))
    recorder.write(frame(2, 80.001))  # 30 s later
    recorder.write(frame(3, 80.101))
    recorder.close()
    assert frames_in(path) == 5


class GatedWriter:
    """Stands in for cv2.VideoWriter: its first write blocks until the test opens the gate."""

    def __init__(self, *args):
        self.gate, self.entered, self.written = threading.Event(), threading.Event(), []

    def isOpened(self):
        return True

    def write(self, image):
        self.entered.set()
        assert self.gate.wait(10)
        self.written.append(int(image[0, 0, 0]))  # numbered() stores the frame number in the pixels

    def release(self):
        pass


def numbered(seq: int) -> Frame:
    """Frame `seq` of a 10 FPS pipeline, recognisable by its pixels."""
    late = 0.001 if seq > 1 else 0.0  # keeps the timestamps off the exact boundaries between video frames
    return Frame(np.full((48, 64, 3), seq, np.uint8), 50.0 + 0.1 * (seq - 1) + late, seq)


def gated_recorder(monkeypatch, tmp_path) -> tuple[Recorder, GatedWriter]:
    writer = GatedWriter()
    monkeypatch.setattr(cv2, "VideoWriter", lambda *args: writer)
    recorder = Recorder(tmp_path / "clip.avi", fps=30)
    recorder.write(numbered(1))
    assert writer.entered.wait(10)  # the encoder is now stuck on frame 1
    return recorder, writer


def test_recorder_never_makes_the_pipeline_wait_for_the_encoder(monkeypatch, tmp_path):
    recorder, writer = gated_recorder(monkeypatch, tmp_path)
    started = time.perf_counter()
    for i in range(2, 40):
        recorder.write(numbered(i))
    assert time.perf_counter() - started < 1.0  # the encoder has not written a single frame meanwhile
    assert writer.written == []
    writer.gate.set()
    recorder.close()


def test_recorder_that_falls_behind_stops_repeating_frames_before_it_drops_any(monkeypatch, tmp_path):
    recorder, writer = gated_recorder(monkeypatch, tmp_path)
    for i in range(2, 8):  # six frames pile up behind frame 1
        recorder.write(numbered(i))
    writer.gate.set()
    recorder.close()
    # Frames 2 and 3 are taken with four or more still waiting: written once. After that the backlog is
    # short again and each frame is repeated to cover its 0.1 s. Nothing is dropped.
    assert writer.written == [1, 2, 3] + [4] * 3 + [5] * 3 + [6] * 3 + [7] * 3


def test_recorder_drops_the_oldest_waiting_frames_when_the_encoder_is_hopelessly_slow(monkeypatch, tmp_path):
    recorder, writer = gated_recorder(monkeypatch, tmp_path)
    for i in range(2, 13):  # eleven frames arrive while the encoder is stuck; only eight may wait
        recorder.write(numbered(i))
    writer.gate.set()
    recorder.close()
    assert sorted(set(writer.written)) == [1, 5, 6, 7, 8, 9, 10, 11, 12]


# --- command lines ---

def test_main_rejects_a_missing_config_and_an_unknown_target_label(tmp_path):
    assert vision_main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert vision_main(["--target-label", "unicorn"]) == 2


def test_subscribe_print_reads_and_validates_messages(capsys):
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=FAST) as pub:  # idle, so it sends heartbeats
        assert subscribe_main(["--endpoint", pub.endpoint, "--count", "2", "--timeout", "10"]) == 0
    out = capsys.readouterr().out
    assert out.count("degraded") >= 2 and "2 messages" in out and "0 invalid" in out


def test_format_vision_shows_what_matters():
    msg = {"t": 99.9, "frame_id": 12, "status": "ok", "phase": "approach",
           "target": {"track_id": 3, "label": "cup", "source": "yolo", "bbox": [0.2, 0.4, 0.4, 0.6], "confidence": 0.87},
           "guidance": {"dx": -0.4, "dy": 0.0, "distance_hint": "near", "aligned": False},
           "summary": "Cup ahead, left."}
    line = format_vision(msg, now=100.0)
    for part in ("#12", "100 ms", "ok", "approach", "cup #3 0.87", "dx -0.40", "near", 'SAY "Cup ahead, left."'):
        assert part in line
    assert line.isascii()


def test_format_vision_shows_the_alert():
    msg = {"t": 99.9, "frame_id": 12, "status": "no_target", "phase": "search", "target": None, "guidance": None,
           "summary": "Grip lost. Object may have dropped.", "alert": "dropped"}
    line = format_vision(msg, now=100.0)
    assert "search" in line and "ALERT DROPPED" in line
    assert "ALERT" not in format_vision({**msg, "alert": None}, now=100.0)


def test_subscribe_print_listens_where_each_topic_is_published(shipped_cfg):
    transport = shipped_cfg.transport
    assert default_endpoints("vision", transport) == [transport.vision_pub]
    assert default_endpoints("pressure", transport) == [transport.pressure_sub]
    assert default_endpoints("robot", transport) == [transport.robot_sub]  # to record grasps (gripper_load)
    assert default_endpoints("command", transport) == transport.command_sub


def test_subscribe_print_says_no_guidance_only_for_a_quiet_vision_topic():
    # No commands for a while is normal; only a silent vision topic means a consumer has no guidance.
    assert "no guidance" in quiet_notice("vision")
    for topic in ("command", "pressure"):
        assert "guidance" not in quiet_notice(topic) and topic in quiet_notice(topic)


# --- M2: phases, stations and the gripper pads, through the real pipeline ---

FPS = 32  # a power of two, so frame times are exact
HOLD = Contact(True, 0.7, 0.0)
OPEN = Contact(False, 0.02, 0.0)
CUP_HELD = det("cup", 0.9, cx=0.5, cy=0.7, w=0.3, h=0.3)                     # in the gripper, lower half
CUP_CLOSE = det("cup", 0.9, cx=0.5, cy=0.5, w=0.5, h=0.5)                    # in_reach
DROP_FAR = det("drop station", 0.97, cx=0.6, cy=0.3, w=0.1, h=0.1, source="aruco")
DROP_NEAR = det("drop station", 0.97, cx=0.5, cy=0.35, w=0.25, h=0.25, source="aruco")
PICK = det("pick station", 0.97, cx=0.2, cy=0.2, w=0.15, h=0.15, source="aruco")


class Scene:
    """A detector whose answer the test sets before each frame."""

    description = "scene"

    def __init__(self):
        self.now = []

    def detect(self, image):
        return list(self.now)


class Walk:
    """Drives a real Pipeline frame by frame with a scripted scene and pad contact; keeps every message."""

    def __init__(self, cfg):
        self.scene = Scene()
        self.pipeline = Pipeline(cfg, self.scene)
        self.t, self.seq, self.messages = 1000.0, 0, []

    def frames(self, seconds, detections, contact):
        for _ in range(max(1, round(seconds * FPS))):
            self.seq += 1
            self.t += 1 / FPS
            self.scene.now = detections
            msg = self.pipeline.process(Frame(IMAGE, self.t, self.seq), contact).message
            validate_vision(msg)
            self.messages.append(msg)
        return self.messages[-1]

    @property
    def phases(self):
        return runs_of(m["phase"] for m in self.messages)

    def said(self):
        return [m["summary"] for m in self.messages if m["summary"]]


def assert_targets_fit_the_phase(messages):
    """Invariant 6: carry and place only ever publish the drop station; the other phases never a station."""
    for m in messages:
        target = m["target"]
        if target is None:
            continue
        if m["phase"] in ("carry", "place"):
            assert (target["source"], target["label"]) == ("aruco", "drop station"), m
        else:
            assert target["source"] != "aruco", m


def test_a_whole_task_walks_all_six_phases(cfg):
    w = Walk(cfg)
    w.frames(0.5, [PICK], OPEN)                                  # the pick station is drawn, never a target
    assert w.messages[-1]["status"] == "no_target"
    w.frames(1.0, [CUP_HELD, PICK], OPEN)                        # the cup is locked: approach
    w.frames(0.25, [CUP_HELD], HOLD)                             # the pads close on it: grasp
    w.frames(2.5, [CUP_HELD], HOLD)                              # held for the settle time: carry, no station yet
    carry_blind = [m for m in w.messages if m["phase"] == "carry"]
    assert all(m["target"] is None and m["status"] == "no_target" for m in carry_blind)  # never the held cup
    w.frames(1.0, [CUP_HELD, DROP_FAR, PICK], HOLD)              # the drop station appears, far
    assert w.messages[-1]["target"]["label"] == "drop station"
    assert w.messages[-1]["guidance"]["distance_hint"] == "far"
    w.frames(1.0, [CUP_HELD, DROP_NEAR], HOLD)                   # near for the dwell time: place
    w.frames(0.5, [DROP_NEAR], OPEN)                             # the pads open: release
    w.frames(1.5, [DROP_NEAR], OPEN)                             # after the hold time: search again
    assert w.phases == ["search", "approach", "grasp", "carry", "place", "release", "search"]
    assert_targets_fit_the_phase(w.messages)
    assert all(m["alert"] is None for m in w.messages)
    said = w.said()
    assert said.count("Grip established.") == 1 and said.count("Released.") == 1
    assert "Looking for the drop station." in said and "Target lost." not in said
    # The events are said on the frame of their transition, not after a settle time.
    first = {p: next(m for m in w.messages if m["phase"] == p) for p in ("carry", "release")}
    assert first["carry"]["summary"] == "Grip established."
    assert first["release"]["summary"] == "Released."


def test_letting_go_while_carrying_raises_dropped_and_holds_it(cfg):
    w = Walk(cfg)
    w.frames(1.0, [CUP_HELD], OPEN)
    w.frames(1.0, [CUP_HELD], HOLD)
    w.frames(0.5, [CUP_HELD, DROP_FAR], HOLD)
    assert w.messages[-1]["phase"] == "carry"
    start = len(w.messages)
    w.frames(2.0, [DROP_FAR], OPEN)                              # contact ends away from the drop station
    after = w.messages[start:]
    assert after[0]["phase"] == "search" and after[0]["summary"] == "Grip lost. Object may have dropped."
    alerted = [m["alert"] for m in after]
    held = round(cfg.phase.alert_hold_s * FPS)
    assert alerted == ["dropped"] * held + [None] * (len(after) - held)  # on consecutive messages, then gone
    assert "Grip lost. Object may have dropped." not in [m["summary"] for m in after[1:]]
    assert_targets_fit_the_phase(w.messages)


def test_without_pad_data_only_search_and_approach_occur(cfg):
    # Milestone 1 exactly: no pressure feed, no set_phase.
    w = Walk(cfg)
    for _ in range(3):
        w.frames(1.0, [CUP_CLOSE, DROP_NEAR, PICK], None)
        w.frames(1.0, [DROP_NEAR], None)
    assert set(w.phases) == {"search", "approach"}
    assert all(m["alert"] is None for m in w.messages)
    assert_targets_fit_the_phase(w.messages)
    assert "Target lost." in w.said()  # and no blind spot without the pads to confirm a grasp


def test_the_blind_spot_keeps_approach_quiet_while_the_gripper_closes(cfg):
    w = Walk(cfg)
    w.frames(1.5, [CUP_CLOSE], OPEN)
    assert w.messages[-1]["guidance"]["distance_hint"] == "in_reach"
    start = len(w.messages)
    w.frames(cfg.phase.blind_spot_s - 0.25, [], OPEN)              # the closing gripper hides the cup
    blind = w.messages[start:]
    assert all(m["phase"] == "approach" for m in blind)
    assert all(m["target"] is None for m in blind[round(cfg.selection.coast_s * FPS) + 1:])  # status stays truthful
    assert "Target lost." not in w.said()
    w.frames(0.25, [], HOLD)
    assert w.messages[-1]["phase"] == "grasp"


def test_a_blind_spot_that_ends_without_a_grasp_says_target_lost(cfg):
    w = Walk(cfg)
    w.frames(1.5, [CUP_CLOSE], OPEN)
    w.frames(cfg.phase.blind_spot_s + 1.0, [], OPEN)
    assert w.messages[-1]["phase"] == "search"
    assert w.said()[-1] == "Target lost."


def test_set_phase_carry_without_pads_targets_the_drop_station_and_reaches_place(cfg):
    w = Walk(cfg)
    w.frames(0.5, [CUP_CLOSE, DROP_FAR], None)
    assert w.messages[-1]["target"]["label"] == "cup"
    w.pipeline.set_phase("carry")
    msg = w.frames(1 / FPS, [CUP_CLOSE, DROP_FAR], None)
    assert msg["phase"] == "carry" and msg["target"]["label"] == "drop station"  # on the very next frame
    w.frames(3.0, [CUP_CLOSE, DROP_FAR], None)
    assert w.messages[-1]["phase"] == "carry"                    # held without pad data, and no `dropped`
    w.frames(1.0, [DROP_NEAR], None)
    assert w.messages[-1]["phase"] == "place"
    w.frames(3.0, [], None)
    assert w.messages[-1]["phase"] == "place"                    # sticky, and no release without the pads
    w.pipeline.set_phase("search")
    w.frames(1.0, [CUP_CLOSE, DROP_NEAR], None)
    assert w.messages[-1]["phase"] == "approach" and w.messages[-1]["target"]["label"] == "cup"
    assert all(m["alert"] is None for m in w.messages)
    assert_targets_fit_the_phase(w.messages)


def test_set_phase_search_while_the_pads_read_contact_does_not_bounce_to_grasp(cfg):
    w = Walk(cfg)
    w.frames(0.5, [CUP_HELD], OPEN)
    w.frames(1.0, [CUP_HELD], HOLD)
    assert w.messages[-1]["phase"] == "carry"
    w.pipeline.set_phase("search")
    start = len(w.messages)
    w.frames(2.0, [CUP_HELD], HOLD)
    assert set(runs_of(m["phase"] for m in w.messages[start:])) <= {"search", "approach"}


def test_set_phase_search_while_the_pads_are_undecided_does_not_bounce_to_grasp(cfg):
    # Holding throughout, but undecided on the frame of the command (fresh again just after a gap).
    w = Walk(cfg)
    w.frames(0.5, [CUP_HELD], OPEN)
    w.frames(1.0, [CUP_HELD], HOLD)
    assert w.messages[-1]["phase"] == "carry"
    w.pipeline.set_phase("search")
    start = len(w.messages)
    w.frames(1 / FPS, [CUP_HELD], Contact(None, 0.7, 0.0))
    w.frames(2.0, [CUP_HELD], HOLD)
    assert set(runs_of(m["phase"] for m in w.messages[start:])) <= {"search", "approach"}
    assert "Grip established." not in [m["summary"] for m in w.messages[start:]]


def test_an_unknown_phase_is_refused_by_the_pipeline(cfg):
    with pytest.raises(ValueError, match="unknown phase"):
        Pipeline(cfg, Scene()).set_phase("lunch")


def test_a_camera_hiccup_while_carrying_is_not_a_drop(cfg):
    w = Walk(cfg)
    w.frames(0.5, [CUP_HELD], OPEN)
    w.frames(1.0, [CUP_HELD], HOLD)
    assert w.messages[-1]["phase"] == "carry"
    w.t += 1.0                                                   # a one second gap in the feed, pads still holding
    start = len(w.messages)
    w.frames(0.5, [CUP_HELD], HOLD)
    assert all(m["phase"] == "carry" and m["alert"] is None for m in w.messages[start:])


class Holding:
    """A pressure feed that always reads a firm grip, stamped now."""

    def poll(self):
        return [PressureReading(clock.now(), (2867.0,) * 4, (0.7,) * 4)]


def test_switching_camera_restarts_the_phase_and_the_pads_bring_carry_back(cfg):
    class SwitchingSource(FakeSource):
        generation = 0

        def read(self, timeout_s):
            result = super().read(timeout_s)
            if self._seq == 40:
                self.generation = 1
            return result

    messages = []

    class Publisher:
        def publish(self, message):
            messages.append(message)

    run(cfg, SwitchingSource([0.02] * 100), FakeDetector([CUP_HELD]), Publisher(), pressure=Holding())
    assert messages[38]["phase"] == "carry"
    assert messages[39]["phase"] in ("search", "approach", "grasp")   # a different camera: everything restarts
    after = [m["phase"] for m in messages[39:]]
    first_carry = after.index("carry")
    assert first_carry * 0.02 <= cfg.phase.grasp_settle_s + 0.2      # back to carry within the settle time
    assert all(m["alert"] is None for m in messages)


def test_the_loop_feeds_the_pads_to_the_phase(cfg):
    class ScriptedPads:
        """Open for the first 30 polls, then a firm grip."""

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            level = 0.02 if self.polls <= 30 else 0.7
            return [PressureReading(clock.now(), (level * 4095,) * 4, (level,) * 4)]

    seen = []
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=cfg.watchdog) as pub:
        run(cfg, FakeSource([0.01] * 120), FakeDetector([CUP_HELD]), pub, pressure=ScriptedPads(),
            on_result=lambda frame, result: seen.append((result.message["phase"], result.contact)))
    assert runs_of(p for p, _ in seen)[-3:] == ["approach", "grasp", "carry"]
    assert seen[-1][1].holding


class PadFeed:
    """Like PressureReceiver with pads at 50 Hz: every sample stamped since the last poll, in order, or None."""

    def __init__(self, level_at):
        self._level_at = level_at  # seconds since the first poll -> grip level
        self._start = self._next = None

    def poll(self):
        now = clock.now()
        if self._start is None:
            self._start = self._next = now
        readings = []
        while self._next <= now:
            level = self._level_at(self._next - self._start)
            readings.append(PressureReading(self._next, (level * 4095,) * 4, (level,) * 4))
            self._next += 0.02
        return readings or None


def test_a_loop_slower_than_the_pad_stale_time_still_grasps_carries_and_sees_the_drop(shipped_cfg):
    # Every pass takes longer than pressure.stale_s (a slow detector) while the pads publish at 50 Hz. Shipped
    # watchdog: a 0.3 s gap between frames is not a stall there.
    class SlowDetector(FakeDetector):
        def detect(self, image):
            time.sleep(0.3)
            return super().detect(image)

    messages, seen = [], []

    class Publisher:
        def publish(self, message):
            messages.append(message)

    feed = PadFeed(lambda s: 0.7 if 0.6 <= s < 2.4 else 0.02)  # open, a firm grip for 1.8 s, then let go
    run(shipped_cfg, FakeSource([0.0] * 15), SlowDetector([CUP_HELD]), Publisher(), pressure=feed,
        on_result=lambda frame, result: seen.append((frame.t, result.contact)))
    assert min(b[0] - a[0] for a, b in zip(seen, seen[1:])) > shipped_cfg.pressure.stale_s  # the premise
    states = [c.state for _, c in seen]
    first = next(i for i, s in enumerate(states) if s is not None)
    assert None not in states[first:]  # the feed never stopped, so the contact never goes unknown again
    phases = runs_of(m["phase"] for m in messages)
    grasp = phases.index("grasp")
    assert phases[grasp + 1] == "carry" and phases[grasp + 2] in ("search", "approach")
    dropped = [m for m in messages if m["alert"] == "dropped"]
    assert dropped and dropped[0]["summary"] == "Grip lost. Object may have dropped."


# --- the gripper servo's load as the grip signal (gripper_load.py) ---

GRIPPER_LOAD = GripperLoadConfig(enabled=True, sign=1, on_threshold=0.3, off_threshold=0.1, on_hold_s=0.2,
                                 off_hold_s=0.3, stale_s=0.35, empty_pos=None, empty_margin=3.0)


class RobotFeed:
    """Like GripperLoadReceiver with the arm controller at 10 Hz: every `robot` message since the last poll,
    read with reading_from_robot, or None. load_at: seconds since the first poll -> the gripper's load, or None
    for a message without load (the bus read failed)."""

    def __init__(self, load_at, cfg=GRIPPER_LOAD):
        self._load_at, self._cfg = load_at, cfg
        self._start = self._next = None

    def poll(self):
        now = clock.now()
        if self._start is None:
            self._start = self._next = now
        readings = []
        while self._next <= now:
            load = self._load_at(self._next - self._start)
            msg = {"t": self._next, "pos": [0.0] * 5 + [40.0],
                   "load": None if load is None else [0.0] * 5 + [load],
                   "control": {"input": "potentiometer", "enabled": True, "input_fresh": True,
                               "feedback": "measured", "target": None}}
            reading = reading_from_robot(msg, self._cfg)
            if reading is not None:
                readings.append(reading)
            self._next += 0.1
        return readings or None


def run_with_the_gripper_load(cfg, feed, frames):
    messages = []

    class Publisher:
        def publish(self, message):
            messages.append(message)

    run(cfg, FakeSource([0.01] * frames), FakeDetector([CUP_HELD]), Publisher(), pressure=feed,
        contacts=ContactDetector(GRIPPER_LOAD, level_of=grip_level_of))
    for m in messages:
        validate_vision(m)
    return messages


def test_the_gripper_load_grasps_carries_and_sees_the_drop(cfg):
    # Open, a firm grip from 0.6 s to 2.4 s, then let go while carrying the cup away from the drop station.
    feed = RobotFeed(lambda s: 0.6 if 0.6 <= s < 2.4 else 0.02)
    messages = run_with_the_gripper_load(cfg, feed, 330)
    phases = runs_of(m["phase"] for m in messages)
    grasp = phases.index("grasp")
    assert phases[grasp - 1] == "approach" and phases[grasp + 1] == "carry"
    assert phases[grasp + 2] in ("search", "approach")
    dropped = [m for m in messages if m["alert"] == "dropped"]
    assert dropped and dropped[0]["summary"] == "Grip lost. Object may have dropped."
    assert "Grip established." in [m["summary"] for m in messages]


def test_without_a_measured_gripper_load_only_search_and_approach_occur(cfg):
    # A robot feed whose messages carry no load (a dry run, or the bus read fails): contact stays unknown.
    messages = run_with_the_gripper_load(cfg, RobotFeed(lambda s: None), 150)
    assert set(m["phase"] for m in messages) <= {"search", "approach"}
    assert "approach" in {m["phase"] for m in messages}
    assert all(m["alert"] is None for m in messages)


def test_heartbeats_during_a_stall_keep_the_phase(cfg):
    commands = ScriptedCommands([{"type": "set_phase", "phase": "carry"}])
    source = FakeSource([0.005] * 5 + [("stall", 0.5)] + [0.005] * 5)
    received = run_and_collect_with(cfg, source, FakeDetector([CUP_HELD, DROP_FAR]), commands=commands)
    degraded = [m for m in received if m["status"] == "degraded"]
    assert len(degraded) >= 2
    assert all(m["phase"] == "carry" and m["alert"] is None for m in degraded)
    frames = [m for m in received if m["status"] != "degraded"]
    assert [m["frame_id"] for m in frames] == list(range(1, 11))
    assert all(m["phase"] == "carry" and m["target"]["label"] == "drop station" for m in frames)


def run_and_collect_with(cfg, source, detector, **kwargs) -> list[dict]:
    """run_and_collect, with extra arguments for run()."""
    with VisionPublisher(ANY_PORT, send_hwm=200, watchdog=cfg.watchdog) as pub, Subscriber(pub.endpoint) as sub:
        deadline = time.monotonic() + 5
        while not sub.recv(0.05):
            assert time.monotonic() < deadline, "subscriber never connected"
        run(cfg, source, detector, pub, **kwargs)
        finished = clock.now()
        received = []
        while (item := sub.recv(0.1)) is not None:
            received.append(item[1])
    for msg in received:
        validate_vision(msg)
    received = [m for m in received if m["t"] <= finished]
    first = next(i for i, m in enumerate(received) if m["frame_id"] >= 1 and m["status"] != "degraded")
    return received[first:]
