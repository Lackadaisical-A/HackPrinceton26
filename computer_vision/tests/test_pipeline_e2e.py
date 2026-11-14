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
from paul_vision.config import WatchdogConfig
from paul_vision.messages import validate_vision
from paul_vision.subscribe_print import format_vision, main as subscribe_main
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
