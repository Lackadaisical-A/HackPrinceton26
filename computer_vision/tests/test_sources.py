import dataclasses
import time

import cv2
import numpy as np
import pytest

from paul_vision import sources
from paul_vision.sources import CameraSource, VideoFileSource, open_source

FPS = 50
FRAMES = 10


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A 10-frame, 50 FPS clip whose frame n is a flat grey of brightness 20 * (n + 1)."""
    path = tmp_path_factory.mktemp("clips") / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (64, 48))
    assert writer.isOpened()
    for n in range(FRAMES):
        writer.write(np.full((48, 64, 3), 20 * (n + 1), np.uint8))
    writer.release()
    return path


def frame_number(frame) -> int:
    return round(float(frame.image.mean()) / 20)


def read_all(source, limit=200):
    frames = []
    for _ in range(limit):
        frame = source.read(0.05)
        if frame is None:
            break
        frames.append(frame)
    return frames


def test_clip_plays_once_in_real_time_and_then_ends(clip):
    source = VideoFileSource(clip, fallback_fps=30, at_end="exit")
    started = time.perf_counter()
    frames = read_all(source)
    elapsed = time.perf_counter() - started
    assert not source.ended or frames  # nothing ended before reading
    assert [f.seq for f in frames] == list(range(1, FRAMES + 1))
    assert [frame_number(f) for f in frames] == list(range(1, FRAMES + 1))
    assert frames[0].image.shape == (48, 64, 3)
    assert 0.15 < elapsed < 1.0   # 10 frames at 50 FPS take about 0.2 s, not "as fast as possible"
    assert source.ended and source.read(0.01) is None
    source.close()


def test_frames_carry_capture_time(clip):
    source = VideoFileSource(clip, fallback_fps=30, at_end="exit")
    frames = read_all(source)
    times = [f.t for f in frames]
    assert times == sorted(times)
    assert abs(times[-1] - time.time()) < 1.0  # Unix seconds
    assert 0.1 < times[-1] - times[0] < 0.5
    source.close()


def test_a_slow_consumer_skips_frames_like_a_camera_would(clip):
    source = VideoFileSource(clip, fallback_fps=30, at_end="exit")
    frames = []
    while (frame := source.read(0.05)) is not None:
        frames.append(frame)
        time.sleep(0.05)  # 2.5 frame periods of "inference"
    seqs = [f.seq for f in frames]
    assert len(frames) < FRAMES
    assert seqs == sorted(set(seqs)) and seqs[-1] <= FRAMES
    assert [frame_number(f) for f in frames] == seqs  # seq names the frame of the clip, gaps included
    source.close()


def test_loop_restarts_the_clip_and_keeps_counting(clip):
    source = VideoFileSource(clip, fallback_fps=30, at_end="loop")
    frames = [source.read(0.05) for _ in range(FRAMES + 5)]
    assert all(f is not None for f in frames) and not source.ended
    assert [f.seq for f in frames] == list(range(1, FRAMES + 6))
    assert [frame_number(f) for f in frames[FRAMES:]] == [1, 2, 3, 4, 5]
    source.close()


def test_hold_goes_silent_at_the_end_without_ending(clip):
    source = VideoFileSource(clip, fallback_fps=30, at_end="hold")
    assert len(read_all(source)) == FRAMES
    started = time.perf_counter()
    assert source.read(0.05) is None and source.read(0.05) is None
    assert time.perf_counter() - started >= 0.09  # it waits out the timeout, like a camera with no frames
    assert not source.ended
    source.close()


def test_missing_or_empty_file_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="Could not open"):
        VideoFileSource(tmp_path / "nope.mp4", fallback_fps=30, at_end="exit")
    empty = tmp_path / "empty.avi"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError):
        VideoFileSource(empty, fallback_fps=30, at_end="loop").read(0.01)


def test_open_source_chooses_by_spec(shipped_cfg, clip, monkeypatch):
    opened = []
    monkeypatch.setattr(sources, "CameraSource", lambda index, cfg: opened.append(index) or "camera")
    cfg = dataclasses.replace(shipped_cfg, camera=dataclasses.replace(shipped_cfg.camera, wrist_index=3))
    assert open_source("wrist", cfg) == "camera"
    assert open_source("0", cfg) == "camera"
    assert open_source(2, cfg) == "camera"
    assert opened == [3, 0, 2]
    video = open_source(str(clip), cfg)
    assert isinstance(video, VideoFileSource)
    video.close()


class FakeCamera:
    """Stands in for cv2.VideoCapture: silent for `start_s` after opening, then `frames` frames, then silent."""

    def __init__(self, start_s=0.0, frames=None):
        self.opened_at = time.monotonic()
        self.start_s = start_s
        self.left = frames
        self.released = False

    def grab(self):
        time.sleep(0.005)  # a real camera makes the caller wait for the next frame
        if time.monotonic() - self.opened_at < self.start_s or self.left == 0:
            return False
        if self.left is not None:
            self.left -= 1
        return True

    def retrieve(self):
        return True, np.zeros((48, 64, 3), np.uint8)

    def release(self):
        self.released = True


@pytest.fixture
def cameras(monkeypatch):
    """Makes CameraSource open the FakeCameras queued here, in order, and lists the ones it opened."""
    queued, opened = [], []

    def open_next(index, cfg):
        camera = queued.pop(0)()
        opened.append(camera)
        return camera

    monkeypatch.setattr(sources, "open_capture", open_next)
    return queued, opened


def camera_cfg(shipped_cfg, reopen_after_s, first_frame_s):
    return dataclasses.replace(shipped_cfg.camera, reopen_after_s=reopen_after_s, first_frame_s=first_frame_s)


def test_a_camera_that_is_slow_to_start_is_not_reopened(shipped_cfg, cameras):
    queued, opened = cameras
    queued.append(lambda: FakeCamera(start_s=0.3))  # longer than reopen_after_s, shorter than first_frame_s
    source = CameraSource(0, camera_cfg(shipped_cfg, reopen_after_s=0.1, first_frame_s=2.0))
    try:
        assert source.read(1.5) is not None
        assert len(opened) == 1
    finally:
        source.close()


def test_a_camera_that_never_starts_is_reopened_after_first_frame_s(shipped_cfg, cameras):
    queued, opened = cameras
    queued += [lambda: FakeCamera(start_s=60.0), FakeCamera]
    started = time.monotonic()
    source = CameraSource(0, camera_cfg(shipped_cfg, reopen_after_s=0.05, first_frame_s=0.3))
    try:
        assert source.read(2.0) is not None
        assert time.monotonic() - started >= 0.3  # not after reopen_after_s
        assert len(opened) == 2 and opened[0].released
    finally:
        source.close()


def test_a_running_camera_that_stops_is_reopened_after_reopen_after_s(shipped_cfg, cameras):
    queued, opened = cameras
    queued += [lambda: FakeCamera(frames=3), FakeCamera]
    source = CameraSource(0, camera_cfg(shipped_cfg, reopen_after_s=0.1, first_frame_s=30.0))
    try:
        assert source.read(1.0) is not None
        deadline = time.monotonic() + 3.0  # far less than first_frame_s: only reopen_after_s can explain a reopen
        while len(opened) < 2 and time.monotonic() < deadline:
            source.read(0.05)
        assert len(opened) == 2 and opened[0].released
        assert source.read(1.0) is not None  # frames again, from the reopened camera
    finally:
        source.close()
