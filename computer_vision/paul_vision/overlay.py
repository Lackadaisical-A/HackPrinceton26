"""Debug overlay window. Drawing is separate from showing, so it can be tested without a display."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .aruco import SOURCE_ARUCO
from .config import Config
from .geometry import center
from .types import STATUS_LOW_CONFIDENCE, STATUS_OK, Guidance, Track

if TYPE_CHECKING:
    from .pressure import Contact

log = logging.getLogger(__name__)

WINDOW = "PAUL vision  (q quit, s snapshot, p pause)"
QUIT, SNAPSHOT, PAUSE = "quit", "snapshot", "pause"

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_GREEN, _YELLOW, _RED, _GREY, _WHITE = (80, 220, 80), (0, 215, 255), (60, 60, 230), (170, 170, 170), (255, 255, 255)
_MAGENTA = (230, 80, 230)  # station markers that are not the target: they must not look like objects
_STATUS_COLOURS = {STATUS_OK: _GREEN, STATUS_LOW_CONFIDENCE: _YELLOW}


def grip_line(contact: Contact | None, source: str = "pads") -> tuple[str, tuple[int, int, int]]:
    """What the grip signal says, for the overlay: state and level, or that there is no data.

    source: "pads" (the `pressure` topic) or "load" (the gripper servo's load, gripper_load.py).
    """
    name, data = ("grip (load)", "no load data") if source == "load" else ("grip", "no pressure data")
    if contact is None or not contact.fresh:
        return f"{name}: {data}", _GREY
    if contact.state is None:
        return f"{name}: deciding {contact.level:.2f}", _YELLOW
    if contact.state:
        return f"{name}: holding {contact.level:.2f}", _GREEN
    return f"{name}: open {contact.level:.2f}", _WHITE


_STATUS_LINE_HEIGHT = 34  # labels stay out of this strip at the top of the window


def _text_width(text: str, scale: float) -> int:
    return cv2.getTextSize(text, _FONT, scale, 1)[0][0]


def _text(image: np.ndarray, text: str, origin: tuple[int, int], colour=_WHITE, scale: float = 0.55) -> None:
    """Text on a darkened plate, readable on any background.

    (Not an outline: OpenCV 5 draws a thicker stroke as a heavier, wider font, so an outline made
    from a thick stroke under a thin one no longer lines up.)
    """
    (text_w, text_h), baseline = cv2.getTextSize(text, _FONT, scale, 1)
    x, y = origin
    x1, y1 = max(0, x - 4), max(0, y - text_h - 5)
    x2, y2 = min(image.shape[1], x + text_w + 4), min(image.shape[0], y + baseline + 3)
    if x2 > x1 and y2 > y1:
        plate = image[y1:y2, x1:x2]
        plate[:] = plate // 4
    cv2.putText(image, text, origin, _FONT, scale, colour, 1, cv2.LINE_AA)


def _label_origin(x1: int, y1: int) -> tuple[int, int]:
    """Above the box, or just inside it when the box reaches the status line."""
    return (x1 + 4, y1 - 7) if y1 - 7 > _STATUS_LINE_HEIGHT + 16 else (x1 + 6, max(y1, _STATUS_LINE_HEIGHT) + 20)


def _pixels(bbox, width: int, height: int) -> tuple[int, int, int, int]:
    return int(bbox[0] * width), int(bbox[1] * height), int(bbox[2] * width), int(bbox[3] * height)


def render(image: np.ndarray, cfg: Config, *, status: str, phase: str, tracks: list[Track] = (),
           target: Track | None = None, guidance: Guidance | None = None, coasting: bool = False,
           summary: str | None = None, fps: float = 0.0, latency_ms: float = 0.0,
           banner: str | None = None, pressure_feed: bool = False, grip_source: str = "pads",
           contact: Contact | None = None, alert: str | None = None, forced: bool = False) -> np.ndarray:
    """A downscaled copy of the frame with everything the pipeline decided drawn on it.

    pressure_feed: a grip feed is configured, so the grip line is drawn (from `contact`); grip_source says which
    ("pads" or "load", the gripper servo's load). alert: drawn as a
    banner while the message carries it. forced: the phase was set by command.
    """
    scale = min(1.0, cfg.overlay.max_width / image.shape[1])
    if scale < 1.0:
        canvas = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        canvas = image.copy()
    height, width = canvas.shape[:2]
    cx, cy = width // 2, height // 2

    # Frame centre and the zone in which the target counts as aligned.
    radii = (max(1, int(cfg.guidance.deadzone_enter * width / 2)), max(1, int(cfg.guidance.deadzone_enter * height / 2)))
    aligned = bool(guidance and guidance.aligned)
    cv2.ellipse(canvas, (cx, cy), radii, 0, 0, 360, _GREEN if aligned else _GREY, 1, cv2.LINE_AA)
    cv2.drawMarker(canvas, (cx, cy), _GREEN if aligned else _GREY, cv2.MARKER_CROSS, 16, 1)

    for tr in tracks:
        if target is not None and tr.track_id == target.track_id:
            continue
        x1, y1, x2, y2 = _pixels(tr.bbox, width, height)
        colour = _MAGENTA if tr.source == SOURCE_ARUCO else _GREY
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 1)
        _text(canvas, f"{tr.label} {tr.confidence:.2f} #{tr.track_id}", _label_origin(x1, y1), colour, 0.45)

    if target is not None:
        colour = _YELLOW if coasting else _GREEN
        x1, y1, x2, y2 = _pixels(target.bbox, width, height)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)
        if guidance is not None and not guidance.aligned:
            # The arrow shows the published (smoothed) offset: from the centre towards the target.
            tip = (int(cx + guidance.dx * width / 2), int(cy + guidance.dy * height / 2))
            cv2.arrowedLine(canvas, (cx, cy), tip, colour, 2, cv2.LINE_AA, tipLength=0.15)
        else:
            tx, ty = center(target.bbox)
            cv2.circle(canvas, (int(tx * width), int(ty * height)), 4, colour, -1, cv2.LINE_AA)
        # Drawn after the arrow so the label stays readable.
        _text(canvas, f"{target.label} {target.confidence:.2f} #{target.track_id}", _label_origin(x1, y1), colour)

    line = f"{status.upper()}  {phase}" + (" (set)" if forced else "")
    if guidance is not None:
        line += f"  dx {guidance.dx:+.2f} dy {guidance.dy:+.2f}  {guidance.distance_hint}"
        line += "  ALIGNED" if guidance.aligned else ""
        line += "  (coasting)" if coasting else ""
    _text(canvas, line, (10, 24), _STATUS_COLOURS.get(status, _RED), 0.6)
    timing = f"{fps:.0f} FPS  {latency_ms:.0f} ms"
    _text(canvas, timing, (width - _text_width(timing, 0.5) - 10, 24), _WHITE, 0.5)
    if pressure_feed:
        grip, grip_colour = grip_line(contact, grip_source)
        _text(canvas, grip, (10, 50), grip_colour, 0.5)
    if summary:
        _text(canvas, f'"{summary}"', (10, height - 14), _WHITE, 0.6)
    if alert:
        text = f"ALERT: {alert.upper()}"
        _text(canvas, text, ((width - _text_width(text, 1.0)) // 2, cy + 60), _RED, 1.0)
    if banner:
        _text(canvas, banner, ((width - _text_width(banner, 0.8)) // 2, cy - 40), _RED, 0.8)
    return canvas


class OverlayWindow:
    """Shows rendered frames and turns key presses into actions."""

    def __init__(self, snapshot_dir: str | Path = "snapshots"):
        self._snapshot_dir = Path(snapshot_dir)
        self._shown = False
        self._last: np.ndarray | None = None

    def show(self, canvas: np.ndarray, wait_ms: int = 1) -> str | None:
        """Display a frame and return QUIT, SNAPSHOT, PAUSE or None."""
        self._last = canvas
        cv2.imshow(WINDOW, canvas)
        self._shown = True
        return self._action(cv2.waitKey(wait_ms))

    def idle(self, wait_ms: int) -> str | None:
        """Keep the window responsive while there is nothing new to show."""
        if not self._shown:
            time.sleep(wait_ms / 1000)
            return None
        return self._action(cv2.waitKey(wait_ms))

    def _action(self, key: int) -> str | None:
        if key != -1:
            key &= 0xFF  # some platforms put modifier bits above the character code
        if key in (ord("q"), ord("Q"), 27):
            return QUIT
        if key in (ord("s"), ord("S")):
            return SNAPSHOT
        if key in (ord("p"), ord("P"), ord(" ")):
            return PAUSE
        if self._shown and cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            return QUIT  # the window was closed with the mouse
        return None

    def snapshot(self, raw: np.ndarray | None) -> None:
        """Save the clean frame and what the overlay showed for it."""
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        stem = self._snapshot_dir / time.strftime("%Y%m%d-%H%M%S")
        if raw is not None:
            cv2.imwrite(f"{stem}-frame.jpg", raw)
        if self._last is not None:
            cv2.imwrite(f"{stem}-overlay.jpg", self._last)
        log.info("snapshot saved: %s-*.jpg", stem)

    def close(self) -> None:
        if self._shown:
            cv2.destroyAllWindows()
            self._shown = False
