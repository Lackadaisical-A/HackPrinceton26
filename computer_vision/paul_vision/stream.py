"""The camera picture for the web console: MJPEG over HTTP, served by this process.

On Windows a camera that this process holds open is not available to a second one, so the console
cannot open it itself. It gets the frames from here, as `multipart/x-mixed-replace` JPEGs that a
plain <img> element plays.

Guidance comes first: `submit` only swaps a reference, each picture is encoded once on its own
thread however many viewers there are, and nothing is encoded while no one is watching.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .config import StreamConfig

if TYPE_CHECKING:
    from .camera_control import CameraControl

log = logging.getLogger(__name__)

STREAM_PATH = "/stream"
_BOUNDARY = b"frame"
_LOOPBACK_NAMES = ("127.0.0.1", "localhost")
_SEND_TIMEOUT_S = 5.0  # a viewer that accepts no data for this long is dropped
_LINGER_S = 2.0  # after the answer, wait at most this long for the client to close its end...
_LINGER_MAX_BYTES = 64 * 1024  # ...and drop at most this much of what it still sends


def _host_name(header: str | None) -> str:
    """The name in a Host header, without the port: '127.0.0.1:8766' -> '127.0.0.1'."""
    host = (header or "").strip().lower()
    return host.rpartition(":")[0] if ":" in host else host


class FrameStream:
    """Serves the newest frame to every connected viewer."""

    def __init__(self, cfg: StreamConfig, *, camera_control: CameraControl | None = None):
        self._cfg = cfg
        self.camera_control = camera_control
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)  # wakes the encoder
        self._new_jpeg = threading.Condition(self._lock)   # wakes the viewers
        self._image: np.ndarray | None = None  # the newest frame that is not encoded yet
        self._jpeg = b""
        self._jpeg_seq = 0
        self._viewers = 0
        self._closing = False
        self._encode_failed = False
        self._server: _Server | None = None
        self._threads: list[threading.Thread] = []
        self.port = cfg.port  # replaced by the real port once bound (matters for port 0)
        self.encoded = 0      # pictures encoded so far

    @property
    def url(self) -> str:
        return f"http://{self._cfg.host}:{self.port}{STREAM_PATH}"

    @property
    def viewers(self) -> int:
        with self._lock:
            return self._viewers

    def start(self) -> "FrameStream":
        """Bind the port and start serving. Raises OSError if the port cannot be bound."""
        server = _Server((self._cfg.host, self._cfg.port), _Handler)
        server.stream = self
        self._server = server
        self.port = server.server_address[1]
        self._threads = [
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                             name="stream-http", daemon=True),
            threading.Thread(target=self._encode_loop, name="stream-encode", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        log.info("camera stream for the console on %s (up to %d px wide, %.0f fps)",
                 self.url, self._cfg.max_width, self._cfg.max_fps)
        return self

    def submit(self, image: np.ndarray) -> None:
        """Offer the newest frame. Returns at once; the frame is used only while someone is watching."""
        with self._lock:
            if self._viewers:
                self._image = image
                self._new_frame.notify()

    def close(self) -> None:
        if self._server is None:
            return
        with self._lock:
            self._closing = True
            self._new_frame.notify_all()
            self._new_jpeg.notify_all()
        self._server.shutdown()
        self._server.server_close()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._server = None

    def __enter__(self) -> "FrameStream":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def host_allowed(self, header: str | None) -> bool:
        """False for a request that reached this server under a name that is not its own.

        A page on another site may not read these pictures, but it could point a name of its own
        at 127.0.0.1 and ask again as "the same site". Such a request carries that name in its Host
        header. The check is skipped when stream.host is a network address: the camera is then
        meant for other computers, under names this server cannot know.
        """
        if self._cfg.host not in _LOOPBACK_NAMES:
            return True
        return _host_name(header) in _LOOPBACK_NAMES

    def feed(self, wfile) -> None:
        """Write pictures to one viewer until it leaves or the stream closes. Runs on the viewer's thread."""
        with self._lock:
            self._viewers += 1
            watching = self._viewers
            seen = self._jpeg_seq  # start with the next picture: a viewer is never shown an old one as new
        log.info("camera stream: a viewer connected (%d watching)", watching)
        try:
            wfile.write(b"--" + _BOUNDARY + b"\r\n")
            while True:
                with self._lock:
                    while self._jpeg_seq == seen and not self._closing:
                        self._new_jpeg.wait()
                    if self._closing:
                        return
                    seen, jpeg = self._jpeg_seq, self._jpeg
                # The boundary goes out with the picture it ends, not with the next one: a browser
                # shows a picture only once it has seen the boundary after it.
                wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg)
                            + jpeg + b"\r\n--" + _BOUNDARY + b"\r\n")
        except OSError:
            pass  # the viewer went away, or stopped reading
        finally:
            with self._lock:
                self._viewers -= 1
                watching = self._viewers
            log.info("camera stream: a viewer left (%d watching)", watching)

    def _encode_loop(self) -> None:
        interval_s = 1.0 / self._cfg.max_fps
        next_at = 0.0
        while True:
            with self._lock:
                # Wait for a frame, then for the rate limit. A frame that arrives during the second
                # wait replaces the waiting one, so the newest frame is the one encoded.
                while not self._closing:
                    if self._image is None:
                        self._new_frame.wait()
                        continue
                    wait_s = next_at - time.monotonic()
                    if wait_s <= 0:
                        break
                    self._new_frame.wait(wait_s)
                if self._closing:
                    return
                image, self._image = self._image, None
            next_at = time.monotonic() + interval_s
            jpeg = self._encode(image)
            if jpeg is None:
                continue
            with self._lock:
                self._jpeg = jpeg
                self._jpeg_seq += 1
                self.encoded += 1
                self._new_jpeg.notify_all()

    def _encode(self, image: np.ndarray) -> bytes | None:
        try:
            height, width = image.shape[:2]
            if width > self._cfg.max_width:
                size = (self._cfg.max_width, max(1, round(height * self._cfg.max_width / width)))
                image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
            ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._cfg.jpeg_quality])
            if not ok:
                raise RuntimeError("the JPEG encoder refused the frame")
        except Exception:
            if not self._encode_failed:  # once, not thirty times a second
                log.exception("camera stream: a frame could not be encoded; such frames are skipped")
                self._encode_failed = True
            return None
        self._encode_failed = False
        return data.tobytes()


class _Server(ThreadingHTTPServer):
    daemon_threads = True  # a viewer's thread never keeps the process alive
    # On Windows, address reuse would let a second process take the same port without an error.
    allow_reuse_address = os.name != "nt"
    stream: FrameStream

    def server_bind(self) -> None:
        # HTTPServer.server_bind also looks up the host's full name, which can take seconds on Windows.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def shutdown_request(self, request) -> None:
        # Close in stages (RFC 9112, 9.6): end the answer, then read and drop what the client still sends
        # until it closes its end. A refused POST leaves its body unread, and a socket closed with unread
        # data resets the connection; on Windows the reset discards the answer before the client reads it
        # (WinError 10053 instead of the 400, 403, 404 or 503). Bounded in time and bytes: past either,
        # the connection is closed at once.
        try:
            request.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + _LINGER_S
            left = _LINGER_MAX_BYTES
            while left > 0 and (wait_s := deadline - time.monotonic()) > 0:
                request.settimeout(wait_s)
                data = request.recv(min(left, 8192))
                if not data:
                    break  # the client has closed its end
                left -= len(data)
        except OSError:
            pass  # reset, timed out, or already gone
        self.close_request(request)


class _Handler(BaseHTTPRequestHandler):
    server_version = "paul-vision"
    sys_version = ""
    timeout = _SEND_TIMEOUT_S  # applied to the viewer's socket

    def do_GET(self) -> None:
        stream = self.server.stream
        if urlsplit(self.path).path == "/sources":
            if not self._api_allowed():
                return
            control = stream.camera_control
            if control is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Camera selection is unavailable."})
                return
            if parse_qs(urlsplit(self.path).query).get("refresh") == ["1"]:
                control.refresh()
            self._json(HTTPStatus.OK, control.status())
            return
        if not stream.host_allowed(self.headers.get("Host")):
            self._refuse(HTTPStatus.FORBIDDEN, "This stream answers only under its own address.")
            return
        if self.path.partition("?")[0] != STREAM_PATH:  # the query is ignored, so a viewer can ask afresh
            self._refuse(HTTPStatus.NOT_FOUND, f"The camera stream is at {STREAM_PATH}")
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            # No Access-Control-Allow-Origin on purpose: an <img> plays the stream without it, and
            # with it any web page could read the pictures.
            self.end_headers()
        except OSError:
            return
        stream.feed(self.wfile)

    def _api_allowed(self, *, require_origin: bool = False) -> bool:
        if not self.server.stream.host_allowed(self.headers.get("Host")):
            self._refuse(HTTPStatus.FORBIDDEN, "This stream answers only under its own address.")
            return False
        origin = self.headers.get("Origin")
        if not origin and not require_origin:
            return True
        try:
            parsed = urlsplit(origin or "")
            allowed = (parsed.scheme in ("http", "https") and
                       parsed.hostname in (*_LOOPBACK_NAMES, "::1") and
                       parsed.username is None and parsed.password is None and
                       not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            allowed = False
        if not allowed:
            self._refuse(HTTPStatus.FORBIDDEN, "Camera selection is available to the local console only.")
        return allowed

    def do_OPTIONS(self) -> None:
        if urlsplit(self.path).path not in ("/sources", "/source"):
            self._refuse(HTTPStatus.NOT_FOUND, "Unknown camera endpoint.")
        elif self._api_allowed(require_origin=True):
            self._json(HTTPStatus.NO_CONTENT, None)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/source":
            self._refuse(HTTPStatus.NOT_FOUND, "Unknown camera endpoint.")
            return
        if not self._api_allowed(require_origin=True):
            return
        control = self.server.stream.camera_control
        if control is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Camera selection is unavailable."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= 1024:
                raise ValueError("Expected a small JSON request containing a camera id.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected a camera id.")
            control.select(payload.get("id"))
        except (ValueError, UnicodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        self._json(HTTPStatus.ACCEPTED, control.status())

    def _json(self, status: HTTPStatus, payload: dict | None) -> None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Origin")
            if self.headers.get("Origin"):
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _refuse(self, status: HTTPStatus, text: str) -> None:
        body = (text + "\n").encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:
        pass  # viewers are logged by FrameStream; a line per request adds nothing
