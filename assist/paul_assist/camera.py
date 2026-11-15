"""One still picture from vision's MJPEG stream (computer_vision/paul_vision/stream.py).

Vision only encodes pictures while someone watches, so a short-lived viewer is the way to get one.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from collections import deque
from typing import BinaryIO, Callable

log = logging.getLogger(__name__)

MAX_JPEG_BYTES = 8 * 1024 * 1024
_LENGTH = re.compile(rb"content-length:\s*(\d+)", re.IGNORECASE)


def read_first_jpeg(stream: BinaryIO, limit: int = MAX_JPEG_BYTES) -> bytes:
    """The first complete JPEG part of a multipart/x-mixed-replace body. ValueError if there is none."""
    headers = b""
    while True:
        line = stream.readline(4096)
        if not line:
            raise ValueError("the stream ended before a picture arrived")
        if line.strip() == b"":
            match = _LENGTH.search(headers)
            if match:
                size = int(match.group(1))
                if not 0 < size <= limit:
                    raise ValueError(f"picture size {size} is out of range")
                data = stream.read(size)
                if len(data) != size or not data.startswith(b"\xff\xd8"):
                    raise ValueError("the picture was cut short or is not a JPEG")
                return data
            headers = b""
            continue
        headers += line
        if len(headers) > 65536:
            raise ValueError("no picture found in the stream")


def grab_jpeg(url: str, timeout_s: float = 5.0) -> bytes:
    """Connect to the stream, take the first picture, disconnect. Raises OSError or ValueError."""
    request = urllib.request.Request(url, headers={"User-Agent": "paul-assist"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 (a local URL from the operator)
        return read_first_jpeg(response)


class FrameRing:
    """Timestamped pictures from the stream while switched on: the camera's recent past, by wall-clock time.

    Smart replay needs the picture that went with each moment of a recording, and a fresh one each turn.
    Off, it holds no connection, so vision stops encoding.
    """

    def __init__(self, url: str, period_s: float = 0.5, keep: int = 320, timeout_s: float = 5.0,
                 clock: Callable[[], float] = time.time) -> None:
        self._url, self._period_s, self._timeout_s, self._clock = url, period_s, timeout_s, clock
        self._frames: deque[tuple[float, bytes]] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self._on = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._on.set()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="assist-frames", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._on.clear()

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def add(self, wall: float, picture: bytes) -> None:
        with self._lock:
            self._frames.append((wall, picture))

    def nearest(self, wall: float, tolerance_s: float = 1.5) -> bytes | None:
        """The stored picture taken closest to `wall`, if one is within the tolerance."""
        with self._lock:
            if not self._frames:
                return None
            t, picture = min(self._frames, key=lambda frame: abs(frame[0] - wall))
        return picture if abs(t - wall) <= tolerance_s else None

    def latest(self, max_age_s: float = 1.5) -> bytes | None:
        with self._lock:
            if not self._frames:
                return None
            t, picture = self._frames[-1]
        return picture if self._clock() - t <= max_age_s else None

    def _run(self) -> None:
        while True:
            if not self._on.wait(0.2):
                continue
            try:
                request = urllib.request.Request(self._url, headers={"User-Agent": "paul-assist"})
                with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
                    last = 0.0
                    while self._on.is_set():
                        picture = read_first_jpeg(response)  # Every part is read so the stream never backs up.
                        now = self._clock()
                        if now - last >= self._period_s:
                            self.add(now, picture)
                            last = now
            except (OSError, ValueError) as exc:
                log.debug("camera stream unavailable: %s", exc)
                time.sleep(0.5)


def read_jpeg_file(path: str) -> bytes:
    with open(path, "rb") as file:
        data = file.read(MAX_JPEG_BYTES + 1)
    if len(data) > MAX_JPEG_BYTES:
        raise ValueError("the picture is larger than 8 MB")
    return data
