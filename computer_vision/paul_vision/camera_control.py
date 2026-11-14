"""Discover cameras without opening them and switch sources at frame boundaries."""
from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable

import cv2

from .config import Config
from .sources import CameraSource, FrameSource
from .types import Frame

log = logging.getLogger(__name__)


def discover_cameras(backend: str) -> list[dict]:
    from cv2_enumerate_cameras import enumerate_cameras
    api = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}[backend]
    return [{"id": hashlib.sha256(f"{item.backend}:{item.path or item.name}".encode()).hexdigest()[:20],
             "index": item.index, "name": item.name or f"Camera {item.index}"}
            for item in enumerate_cameras(api)]


class CameraControl:
    """HTTP requests prepare a candidate on a worker; read() alone commits it.

    The current source keeps streaming until the candidate supplies a frame.
    Failure leaves the current source untouched. Device IDs survive index changes.
    """

    def __init__(self, source: FrameSource, spec: str | int, cfg: Config, *,
                 discover: Callable[[], list[dict]] | None = None,
                 factory: Callable[[int], FrameSource] | None = None,
                 allow_switch: bool = True):
        self._source = source
        self._cfg = cfg
        self._discover = discover or (lambda: discover_cameras(cfg.camera.backend))
        self._factory = factory or (lambda index: CameraSource(index, cfg.camera))
        self._allow_switch = allow_switch
        self._lock = threading.Lock()
        self._discovery_lock = threading.Lock()
        self._cameras: list[dict] = []
        self._active_id: str | None = None
        self._active_name = source.description
        self._pending_id: str | None = None
        self._prepared: tuple[FrameSource, Frame, dict] | None = None
        self._worker: threading.Thread | None = None
        self._closed = False
        self._error: str | None = None
        self._discovery_error: str | None = None
        self.generation = 0
        self.refresh()
        index = cfg.camera.wrist_index if spec == "wrist" else int(spec) if str(spec).isdigit() else None
        for camera in self._cameras:
            if camera["index"] == index:
                self._active_id, self._active_name = camera["id"], camera["name"]
                break

    @property
    def description(self) -> str:
        return self._source.description

    @property
    def ended(self) -> bool:
        return self._source.ended and self._pending_id is None

    def refresh(self) -> None:
        with self._discovery_lock:
            try:
                cameras = self._discover()
            except Exception as exc:
                log.warning("camera discovery failed: %s", exc)
                with self._lock:
                    self._discovery_error = "Could not list cameras. Check the vision service and try Refresh."
                return
            with self._lock:
                self._cameras = cameras
                self._discovery_error = None

    def status(self) -> dict:
        with self._lock:
            return {"sources": [dict(item) for item in self._cameras],
                    "active_id": self._active_id, "active_name": self._active_name,
                    "pending_id": self._pending_id, "generation": self.generation,
                    "error": self._error or self._discovery_error,
                    "can_switch": self._allow_switch and not self._closed}

    def select(self, camera_id: object) -> None:
        if not isinstance(camera_id, str) or not camera_id:
            raise ValueError("Choose a camera from the available sources.")
        self.refresh()
        with self._lock:
            if self._closed or not self._allow_switch:
                raise RuntimeError("Camera switching is unavailable while video recording is active or vision is stopping.")
            if self._discovery_error:
                raise RuntimeError(self._discovery_error)
            camera = next((item for item in self._cameras if item["id"] == camera_id), None)
            if camera is None:
                raise ValueError("That camera is no longer connected. Refresh the source list.")
            if self._pending_id is not None:
                raise RuntimeError("A camera switch is already in progress.")
            if camera_id == self._active_id:
                self._error = None
                return
            self._pending_id = camera_id
            self._error = None
            self._worker = threading.Thread(target=self._prepare, args=(dict(camera),), daemon=True,
                                            name="camera-switch")
            self._worker.start()

    def _prepare(self, camera: dict) -> None:
        candidate = None
        try:
            candidate = self._factory(camera["index"])
            frame = candidate.read(self._cfg.camera.first_frame_s + 1.0)
            if frame is None:
                raise RuntimeError("no video frames arrived")
            with self._lock:
                if self._closed:
                    return
                self._prepared = (candidate, frame, camera)
                candidate = None
        except Exception as exc:
            log.warning("could not switch to camera %s: %s", camera["name"], exc)
            with self._lock:
                self._error = f"Could not open {camera['name']}. It may be disconnected or in use. Kept the previous source."
                self._pending_id = None
        finally:
            if candidate is not None:
                candidate.close()

    def read(self, timeout_s: float) -> Frame | None:
        with self._lock:
            prepared = self._prepared
            self._prepared = None
        if prepared is not None:
            candidate, first, camera = prepared
            old, self._source = self._source, candidate
            with self._lock:
                self._active_id, self._active_name = camera["id"], camera["name"]
                self._pending_id = None
                self.generation += 1
            old.close()
            log.info("camera switched to %s (index %d)", camera["name"], camera["index"])
            return candidate.read(0) or first
        return self._source.read(timeout_s)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            prepared, self._prepared = self._prepared, None
        if prepared is not None:
            prepared[0].close()
        self._source.close()
        if self._worker is not None:
            self._worker.join(timeout=2)
