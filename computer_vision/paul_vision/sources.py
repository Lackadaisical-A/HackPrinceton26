"""Frame sources: the live camera and video-file replay. Both hand out timestamped frames in gripper view."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from . import clock
from .config import CameraConfig, Config
from .types import Frame

log = logging.getLogger(__name__)

_ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
_BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}


def orient(image: np.ndarray, rotate_deg: int, flip_h: bool, flip_v: bool) -> np.ndarray:
    """Rotate clockwise, then mirror, so that image-right = gripper-right and image-down = gripper-down."""
    if rotate_deg:
        image = cv2.rotate(image, _ROTATIONS[rotate_deg])
    if flip_h or flip_v:
        image = cv2.flip(image, -1 if flip_h and flip_v else 1 if flip_h else 0)
    return image


def fourcc_name(cap: cv2.VideoCapture) -> str:
    """The pixel format the camera is actually delivering, e.g. MJPG or YUY2 ('' if the backend does not say)."""
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    name = "".join(chr((code >> 8 * i) & 0xFF) for i in range(4)) if code > 0 else ""
    return name if name.isprintable() else ""


def open_capture(index: int, cfg: CameraConfig) -> cv2.VideoCapture | None:
    """Open a camera with the configured format, or None if it does not open."""
    cap = cv2.VideoCapture(index, _BACKENDS[cfg.backend])
    if not cap.isOpened():
        cap.release()
        return None
    # The order matters. OpenCV's DirectShow backend renegotiates the stream on every one of these,
    # and forgets the pixel format when the size or rate changes, so the format has to come last.
    # Set first, MJPG is silently dropped and the camera sends raw YUY2: 10 FPS at 720p over USB 2.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
    cap.set(cv2.CAP_PROP_FPS, cfg.fps)
    if cfg.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual = fourcc_name(cap)
    log.info("camera %d open: %dx%d %s @ %.0f fps (asked for %dx%d %s @ %d)", index, width, height,
             actual or "?", cap.get(cv2.CAP_PROP_FPS), cfg.width, cfg.height, cfg.fourcc or "any format", cfg.fps)
    if (width, height) != (cfg.width, cfg.height):
        log.warning("camera %d delivers %dx%d, not the configured %dx%d", index, width, height, cfg.width, cfg.height)
    if cfg.fourcc and actual and actual != cfg.fourcc.upper():
        log.warning("camera %d delivers %s, not the configured %s: expect a low frame rate", index, actual, cfg.fourcc)
    return cap


class FrameSource(Protocol):
    description: str
    ended: bool  # True once the source has nothing more to give and the pipeline should stop

    def read(self, timeout_s: float) -> Frame | None:
        """The next new frame, or None if none arrives within the timeout."""

    def close(self) -> None: ...


class CameraSource:
    """Live camera. A background thread keeps grabbing, so read() always returns the newest frame.

    Frame.seq counts every frame the camera delivered; gaps mean the pipeline skipped frames.
    If the camera stops delivering, the thread keeps trying to reopen it. A camera that was just
    opened is given longer (camera.first_frame_s): one reopened before its first frame never starts.
    """

    ended = False

    def __init__(self, index: int, cfg: CameraConfig):
        self._index = index
        self._cfg = cfg
        self.description = f"camera {index}"
        self._cap = self._open()
        if self._cap is None:
            raise RuntimeError(
                f"Could not open camera {index}. Run `uv run python scripts/list_cameras.py` to see which "
                f"indices work, then set camera.wrist_index in config.yaml or pass --source <index>.")
        self._cond = threading.Condition()
        self._latest: tuple[np.ndarray, float] | None = None
        self._seq = 0
        self._returned_seq = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _open(self) -> cv2.VideoCapture | None:
        return open_capture(self._index, self._cfg)

    def _loop(self) -> None:
        last_ok = time.monotonic()
        allowed_s = self._cfg.first_frame_s  # until the first frame, then reopen_after_s
        while self._running:
            cap = self._cap
            started = time.perf_counter()
            if cap is not None and cap.grab():
                grabbed_t, grabbed = clock.now(), time.perf_counter()
                ok, image = cap.retrieve()
                if ok:
                    # The frame arrived when the call that waited for it returned. That is grab() with
                    # most backends, but DirectShow's grab() returns at once and retrieve() does the waiting.
                    waited_in_grab = grabbed - started >= time.perf_counter() - grabbed
                    t = grabbed_t if waited_in_grab else clock.now()
                    last_ok = time.monotonic()
                    allowed_s = self._cfg.reopen_after_s
                    with self._cond:
                        self._latest = (image, t)
                        self._seq += 1
                        self._cond.notify_all()
                    continue
            if time.monotonic() - last_ok >= allowed_s:
                log.warning("camera %d: no frames for %.1f s, reopening", self._index, allowed_s)
                if cap is not None:
                    cap.release()
                self._cap = self._open()
                last_ok = time.monotonic()
                allowed_s = self._cfg.first_frame_s
            else:
                time.sleep(0.005)

    def read(self, timeout_s: float) -> Frame | None:
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != self._returned_seq, timeout=timeout_s):
                return None
            image, t = self._latest
            seq = self._returned_seq = self._seq
        cfg = self._cfg
        return Frame(orient(image, cfg.rotate_deg, cfg.flip_h, cfg.flip_v), t, seq)

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()


class VideoFileSource:
    """Replays a clip at its own frame rate, skipping frames when the consumer falls behind, like a camera.

    Clips are used as they are: camera orientation settings are not applied (clips recorded with
    --record are already in gripper view). `at_end` is exit, loop or hold; `hold` keeps the source
    open but silent, which looks like a stalled camera to the rest of the pipeline.
    """

    def __init__(self, path: str | Path, fallback_fps: float, at_end: str):
        self._path = str(path)
        self._at_end = at_end
        self._cap = cv2.VideoCapture(self._path)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open video file {self._path}")
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        self._fps = fps if fps and fps > 0 else float(fallback_fps)
        self.description = f"video {Path(self._path).name} @ {self._fps:.0f} fps (at end: {at_end})"
        self.ended = False
        self._holding = False
        self._start: float | None = None  # perf_counter time at which frame 0 of this pass is due
        self._index = 0                   # index, within this pass, of the next frame to decode
        self._seq = 0                     # frames consumed from the file in total, skipped ones included

    def read(self, timeout_s: float) -> Frame | None:
        if self.ended or self._holding:
            time.sleep(timeout_s)
            return None
        if self._start is None:
            self._start = time.perf_counter()
        due_index = int((time.perf_counter() - self._start) * self._fps)
        while self._index < due_index:  # running behind real time: drop frames
            if not self._cap.grab():
                return self._at_end_of_clip(timeout_s)
            self._index += 1
            self._seq += 1
        ok, image = self._cap.read()
        if not ok:
            return self._at_end_of_clip(timeout_s)
        wait = self._start + self._index / self._fps - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        self._index += 1
        self._seq += 1
        return Frame(image, clock.now(), self._seq)

    def _at_end_of_clip(self, timeout_s: float) -> Frame | None:
        if self._index == 0:  # this pass gave nothing: an empty or unreadable file (also stops endless looping)
            raise RuntimeError(f"Video file {self._path} has no readable frames")
        if self._at_end == "loop":
            self._cap.release()
            self._cap = cv2.VideoCapture(self._path)
            self._start, self._index = None, 0
            return self.read(timeout_s)
        if self._at_end == "hold":
            log.warning("video ended: holding with a stalled feed (subscribers now see 'degraded'); quit with q or Ctrl+C")
            self._holding = True
            time.sleep(timeout_s)
            return None
        self.ended = True
        return None

    def close(self) -> None:
        self._cap.release()


def open_source(spec: str | int, cfg: Config) -> FrameSource:
    """`wrist`, a camera index, or a path to a video file."""
    if spec == "wrist":
        return CameraSource(cfg.camera.wrist_index, cfg.camera)
    if isinstance(spec, int) or str(spec).isdigit():
        return CameraSource(int(spec), cfg.camera)
    return VideoFileSource(spec, cfg.camera.fps, cfg.source.video_end)
