"""The pipeline: frame -> detect -> track -> select -> guidance -> message, and the loop around it."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2

from . import clock, overlay
from .commands import CommandReceiver, apply_command
from .config import Config
from .detector import Detector
from .guidance import GuidanceFilter
from .messages import build_vision_message
from .metrics import Metrics
from .phase import PhaseMachine
from .selection import TargetSelector
from .sources import FrameSource
from .summary import Summarizer
from .tracker import IouTracker
from .transport import VisionPublisher
from .types import STATUS_OK, Frame, Guidance, Track

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameResult:
    message: dict
    tracks: list[Track]
    target: Track | None
    guidance: Guidance | None
    coasting: bool


class Pipeline:
    """Everything between a frame and its `vision` message. Holds the state that spans frames."""

    def __init__(self, cfg: Config, detector: Detector):
        self._cfg = cfg
        self._detector = detector
        self._tracker = IouTracker(cfg.tracker)
        self._selector = TargetSelector(cfg.selection)
        self._guidance = GuidanceFilter(cfg.guidance, cfg.distance)
        self._summarizer = Summarizer(cfg.summary)
        self._phase = PhaseMachine()
        self._last_guidance: Guidance | None = None
        self._last_t: float | None = None
        self.detect_s = 0.0  # duration of the last detector call

    def set_preferred_label(self, label: str | None) -> None:
        self._selector.set_preferred_label(label)

    def reset(self) -> None:
        self._tracker.reset()
        self._selector.reset()
        self._guidance.reset()
        self._summarizer.reset()
        self._last_guidance = None
        self._last_t = None
        self._phase = PhaseMachine()

    def process(self, frame: Frame) -> FrameResult:
        # After a gap in the feed the scene may have changed completely: start over rather than
        # glide guidance from where things used to be.
        if self._last_t is not None and frame.t - self._last_t > self._cfg.watchdog.stall_timeout_s:
            log.info("feed resumed after %.1f s: tracking state reset", frame.t - self._last_t)
            self.reset()
        self._last_t = frame.t

        started = time.perf_counter()
        detections = self._detector.detect(frame.image)
        self.detect_s = time.perf_counter() - started

        tracks = self._tracker.update(detections, frame.t)
        sel = self._selector.update(tracks, frame.t)
        guidance = None
        if sel.status == STATUS_OK:
            if sel.fresh or self._last_guidance is None:
                guidance = self._guidance.update(sel.target, frame.t, reset=sel.changed)
            else:
                guidance = self._last_guidance  # coasting over a missed detection: hold the last guidance
        else:
            self._guidance.reset()
        self._last_guidance = guidance

        phase = self._phase.update(sel.status == STATUS_OK)
        label = sel.target.label if sel.target is not None else None
        summary = self._summarizer.update(sel.status, label, guidance, frame.t, target_changed=sel.changed)
        message = build_vision_message(t=frame.t, frame_id=frame.seq, status=sel.status, phase=phase,
                                       target=sel.target, guidance=guidance, summary=summary)
        return FrameResult(message, tracks, sel.target, guidance, coasting=sel.status == STATUS_OK and not sel.fresh)


class Recorder:
    """Writes the frames the pipeline saw to a video file that plays back in real time.

    The file has a fixed frame rate while the pipeline's varies, so each frame is repeated for as
    long as it was the newest one. Encoding runs on its own thread and `write` only hands a frame
    over, so a slow encoder never costs guidance latency. It costs the recording instead: first the
    repeats go (the clip then plays faster than real time), and only if that is not enough, frames.
    Recordings can be replayed with --source <file>.
    """

    _MAX_FILL_S = 1.0  # after a stall, do not write more than this much repeated footage at once
    _CATCH_UP_AT = 4   # with this many frames waiting, each is written once instead of repeated
    _MAX_PENDING = 8   # with this many waiting, the oldest one is dropped

    def __init__(self, path: str | Path, fps: float):
        self._path = Path(path)
        self._fps = float(fps)
        self._writer: cv2.VideoWriter | None = None
        self._t0 = 0.0
        self._written = 0
        self._dropped = 0
        self._caught_up = 0
        self._pending: deque[Frame] = deque()
        self._wake = threading.Condition()
        self._closing = False
        self._thread: threading.Thread | None = None

    def write(self, frame: Frame) -> None:
        """Queue a frame for encoding. Returns at once; raises if the file cannot be created."""
        if self._thread is None:
            self._open(frame)  # here rather than on the thread, so a bad path stops the run immediately
            self._thread = threading.Thread(target=self._encode_loop, name="recorder", daemon=True)
            self._thread.start()
        with self._wake:
            if len(self._pending) >= self._MAX_PENDING:
                self._pending.popleft()
                if self._dropped == 0:
                    log.warning("the video encoder cannot keep up even without repeating frames: %s will miss "
                                "frames (guidance is not affected)", self._path.name)
                self._dropped += 1
            self._pending.append(frame)
            self._wake.notify()

    def _open(self, frame: Frame) -> None:
        height, width = frame.image.shape[:2]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = "MJPG" if self._path.suffix.lower() == ".avi" else "mp4v"
        self._writer = cv2.VideoWriter(str(self._path), cv2.VideoWriter_fourcc(*fourcc), self._fps, (width, height))
        if not self._writer.isOpened():
            raise RuntimeError(f"could not open {self._path} for recording (use a .mp4 or .avi path)")
        self._t0 = frame.t
        log.info("recording to %s (%dx%d @ %.0f fps)", self._path, width, height, self._fps)

    def _encode_loop(self) -> None:
        while True:
            with self._wake:
                while not self._pending and not self._closing:
                    self._wake.wait()
                if not self._pending:
                    return
                frame = self._pending.popleft()
                behind = len(self._pending) >= self._CATCH_UP_AT
            try:
                self._encode(frame, behind)
            except Exception:
                log.exception("recording to %s failed; the rest of the run is not recorded", self._path)
                return

    def _encode(self, frame: Frame, behind: bool) -> None:
        due = int((frame.t - self._t0) * self._fps) + 1  # frames the file should hold once this one is in
        if behind:
            if self._caught_up == 0:
                log.warning("the video encoder is slower than real time: %s will play faster than real time "
                            "(guidance is not affected)", self._path.name)
            self._caught_up += 1
        if behind or due - self._written > self._MAX_FILL_S * self._fps:
            # No time to repeat this frame (or a stall not worth filling): write it once and move the
            # file's timeline so that it counts as on time.
            self._t0 += (due - self._written - 1) / self._fps
            due = self._written + 1
        while self._written < due:
            self._writer.write(frame.image)
            self._written += 1

    def close(self) -> None:
        """Encode what is still queued, then finish the file."""
        if self._thread is not None:
            with self._wake:
                self._closing = True
                self._wake.notify()
            self._thread.join()
        if self._writer is not None:
            self._writer.release()
            fast = f", {self._caught_up} written without repeats so it plays fast" if self._caught_up else ""
            dropped = f", {self._dropped} dropped" if self._dropped else ""
            log.info("recording saved: %s (%d frames%s%s)", self._path, self._written, fast, dropped)


def run(cfg: Config, source: FrameSource, detector: Detector, publisher: VisionPublisher, *,
        window: overlay.OverlayWindow | None = None, recorder: Recorder | None = None,
        max_frames: int | None = None, preferred_label: str | None = None,
        on_result: Callable[[Frame, FrameResult], None] | None = None,
        commands: CommandReceiver | None = None) -> Metrics:
    """Run until the source ends, max_frames is reached, or the operator quits. Returns the timings.

    `on_result` is called with every processed frame after its message is published; it must return
    at once. `commands` is polled on every pass (anything with a `poll()` that returns payloads).
    """
    pipeline = Pipeline(cfg, detector)
    if preferred_label is not None:
        pipeline.set_preferred_label(preferred_label)
    metrics = Metrics(cfg.logging.interval_s)
    read_timeout_s = cfg.watchdog.stall_timeout_s / 2
    idle_ms = max(1, int(read_timeout_s * 1000 / 5))
    paused = False
    last_seq: int | None = None  # frames that passed while the model was loading are not counted as skipped
    source_generation = getattr(source, "generation", 0)
    last: tuple[Frame, FrameResult] | None = None
    last_summary: str | None = None
    detector_failing = False

    def draw(frame: Frame, result: FrameResult, banner: str | None = None):
        msg = result.message
        return overlay.render(frame.image, cfg, status=msg["status"], phase=msg["phase"], tracks=result.tracks,
                              target=result.target, guidance=result.guidance, coasting=result.coasting,
                              summary=last_summary, fps=metrics.fps, latency_ms=metrics.latency_ms, banner=banner)

    def handle(action: str | None) -> bool:
        """Apply a key press; True means quit."""
        nonlocal paused
        if action == overlay.QUIT:
            return True
        if action == overlay.SNAPSHOT:
            window.snapshot(last[0].image if last else None)
        elif action == overlay.PAUSE:
            paused = not paused
            log.info("paused: publishing stops, so subscribers will see 'degraded'" if paused else "resumed")
            if paused and last:
                return handle(window.show(draw(*last, banner="PAUSED - subscribers see 'degraded'")))
        return False

    try:
        while max_frames is None or metrics.frames < max_frames:
            metrics.tick()
            if commands is not None:
                for payload in commands.poll():
                    apply_command(payload, cfg.detector.classes, pipeline.set_preferred_label)
            if paused:
                if handle(window.idle(idle_ms)):
                    break
                continue

            started = time.perf_counter()
            frame = source.read(read_timeout_s)
            generation = getattr(source, "generation", 0)
            if generation != source_generation:
                pipeline.reset()
                source_generation = generation
                last_seq, last, last_summary = None, None, None
            if frame is None:
                if source.ended:
                    log.info("source ended")
                    break
                # No frame: nothing is published, and the publisher's watchdog reports the stall by itself.
                if window is not None and handle(window.idle(1)):
                    break
                continue
            metrics.add("wait for frame", time.perf_counter() - started)

            started = time.perf_counter()
            try:
                result = pipeline.process(frame)
            except Exception:
                # A failing detector must look like a stalled feed (no message -> `degraded`), never like "no target".
                if not detector_failing:
                    log.exception("frame %d could not be processed; skipping frames until this recovers", frame.seq)
                    detector_failing = True
                continue
            detector_failing = False
            publisher.publish(result.message)  # before any drawing: guidance must not wait for the debug window
            latency_s = clock.now() - frame.t
            metrics.add("detect", pipeline.detect_s)
            metrics.add("track/select/guide", time.perf_counter() - started - pipeline.detect_s)
            metrics.frame_done(latency_s, result.message["status"],
                               skipped=0 if last_seq is None else max(0, frame.seq - last_seq - 1))
            last_seq = frame.seq
            last = (frame, result)
            last_summary = result.message["summary"] or last_summary
            if result.message["summary"]:
                log.info("summary: %s", result.message["summary"])

            if on_result is not None:
                on_result(frame, result)
            if recorder is not None:
                started = time.perf_counter()
                recorder.write(frame)
                metrics.add("record", time.perf_counter() - started)
            if window is not None:
                started = time.perf_counter()
                quit_requested = handle(window.show(draw(frame, result)))
                metrics.add("overlay", time.perf_counter() - started)
                if quit_requested:
                    break
    except KeyboardInterrupt:
        log.info("interrupted")
    return metrics
