"""The camera stream for the console, over real sockets on a free port."""
import dataclasses
import re
import socket
import time

import cv2
import numpy as np
import pytest

from paul_vision.config import StreamConfig
from paul_vision.stream import STREAM_PATH, FrameStream

CFG = StreamConfig(enabled=True, host="127.0.0.1", port=0, max_width=64, jpeg_quality=80, max_fps=500)


def image(value: int, width: int = 128, height: int = 72) -> np.ndarray:
    return np.full((height, width, 3), value, np.uint8)


def wait_for(condition, seconds: float = 3.0, doing=None) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never became true"
        if doing is not None:
            doing()
        time.sleep(0.005)


class Viewer:
    """A minimal MJPEG client that reads one picture at a time."""

    def __init__(self, stream: FrameStream, path: str = STREAM_PATH, host: str = "127.0.0.1"):
        self._sock = socket.create_connection(("127.0.0.1", stream.port), timeout=5)
        self._buffer = b""
        self._sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        status_line, *lines = self._until(b"\r\n\r\n").split(b"\r\n")
        self.status = int(status_line.split()[1])
        self.headers = {name.strip().lower(): value.strip()
                        for name, _, value in (line.decode().partition(":") for line in lines)}

    def _fill(self, timeout: float) -> None:
        self._sock.settimeout(timeout)
        chunk = self._sock.recv(65536)
        if not chunk:
            raise EOFError("the stream ended")
        self._buffer += chunk

    def _until(self, marker: bytes, timeout: float = 5.0) -> bytes:
        while marker not in self._buffer:
            self._fill(timeout)
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def picture(self, timeout: float = 5.0) -> np.ndarray:
        """The next picture, decoded. TimeoutError if none arrives in time."""
        head = self._until(b"\r\n\r\n", timeout)
        assert b"--frame" in head and b"Content-Type: image/jpeg" in head
        length = int(re.search(rb"Content-Length: (\d+)", head).group(1))
        while len(self._buffer) < length:
            self._fill(timeout)
        data, self._buffer = self._buffer[:length], self._buffer[length:]
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "Viewer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def brightness(picture: np.ndarray) -> int:
    return int(round(float(picture.mean())))


def test_a_viewer_gets_the_submitted_frame_downscaled_to_max_width():
    with FrameStream(CFG) as stream, Viewer(stream) as viewer:
        assert viewer.status == 200
        assert viewer.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        assert viewer.headers["cache-control"] == "no-store"
        wait_for(lambda: stream.viewers == 1)
        stream.submit(image(200))
        picture = viewer.picture()
    assert picture.shape == (36, 64, 3)
    assert abs(brightness(picture) - 200) <= 3


def test_a_frame_narrower_than_max_width_keeps_its_size():
    with FrameStream(dataclasses.replace(CFG, max_width=640)) as stream, Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        stream.submit(image(90))
        assert viewer.picture().shape == (72, 128, 3)


def test_pictures_arrive_in_order_and_each_is_shown_at_once():
    with FrameStream(CFG) as stream, Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        seen = []
        for value in (30, 120, 210):
            stream.submit(image(value))
            # The boundary that ends a picture is sent with it, so nothing waits for the next frame.
            seen.append(brightness(viewer.picture(timeout=2.0)))
    assert [round(v, -1) for v in seen] == [30, 120, 210]


def test_nothing_is_encoded_while_no_one_watches():
    with FrameStream(CFG) as stream:
        for _ in range(20):
            stream.submit(image(10))
        time.sleep(0.1)
        assert stream.encoded == 0 and stream.viewers == 0


def test_a_new_viewer_starts_with_a_new_picture_never_an_old_one():
    with FrameStream(CFG) as stream, Viewer(stream) as first:
        wait_for(lambda: stream.viewers == 1)
        stream.submit(image(40))
        assert abs(brightness(first.picture()) - 40) <= 3
        with Viewer(stream) as second:
            wait_for(lambda: stream.viewers == 2)
            with pytest.raises(TimeoutError):
                second.picture(timeout=0.3)  # the picture from before it connected is not replayed
            stream.submit(image(220))
            assert abs(brightness(second.picture()) - 220) <= 3


def test_each_picture_is_encoded_once_for_all_viewers():
    with FrameStream(CFG) as stream, Viewer(stream) as first, Viewer(stream) as second:
        wait_for(lambda: stream.viewers == 2)
        stream.submit(image(150))
        assert abs(brightness(first.picture()) - 150) <= 3
        assert abs(brightness(second.picture()) - 150) <= 3
        assert stream.encoded == 1


def test_max_fps_limits_the_encoding_and_the_newest_frame_wins():
    with FrameStream(dataclasses.replace(CFG, max_fps=10)) as stream, Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        started = time.monotonic()
        for value in range(40):
            stream.submit(image(value * 5))
            time.sleep(0.01)
        elapsed = time.monotonic() - started
        time.sleep(0.25)  # the last frame is still waiting for its turn
        assert 2 <= stream.encoded <= elapsed * 10 + 2
        pictures = []
        while True:
            try:
                pictures.append(brightness(viewer.picture(timeout=0.2)))
            except TimeoutError:
                break
    assert len(pictures) == stream.encoded
    assert abs(pictures[-1] - 39 * 5) <= 3


def test_submit_never_waits_for_the_encoder(monkeypatch):
    real = cv2.imencode

    def slow(*args):
        time.sleep(0.2)
        return real(*args)

    monkeypatch.setattr(cv2, "imencode", slow)
    with FrameStream(CFG) as stream, Viewer(stream):
        wait_for(lambda: stream.viewers == 1)
        started = time.monotonic()
        for value in range(500):
            stream.submit(image(value % 255))
        assert time.monotonic() - started < 0.15


def test_a_frame_that_cannot_be_encoded_is_skipped_and_the_stream_goes_on(monkeypatch, caplog):
    real = cv2.imencode
    broken = {"left": 2}

    def flaky(*args):
        if broken["left"]:
            broken["left"] -= 1
            raise RuntimeError("encoder fell over")
        return real(*args)

    monkeypatch.setattr(cv2, "imencode", flaky)
    with FrameStream(CFG) as stream, Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        wait_for(lambda: stream.encoded >= 1, doing=lambda: stream.submit(image(77)))
        assert abs(brightness(viewer.picture()) - 77) <= 3
    assert sum("could not be encoded" in r.message for r in caplog.records) == 1  # once, not per frame


def test_other_paths_are_not_found_and_the_query_is_ignored():
    with FrameStream(CFG) as stream:
        with Viewer(stream, path="/") as other:
            assert other.status == 404
        with Viewer(stream, path="/stream/extra") as other:
            assert other.status == 404
        with Viewer(stream, path=STREAM_PATH + "?r=3", host=f"localhost:{stream.port}") as viewer:
            assert viewer.status == 200
            wait_for(lambda: stream.viewers == 1)
            stream.submit(image(60))
            assert viewer.picture().shape == (36, 64, 3)


def test_pictures_cannot_be_read_by_other_web_pages():
    with FrameStream(CFG) as stream:
        # A page on another site that points its own name at this computer is refused...
        with Viewer(stream, host=f"attacker.example:{stream.port}") as rebound:
            assert rebound.status == 403
        # ...and no page is given permission to read the pixels of the pictures it embeds.
        with Viewer(stream) as viewer:
            assert "access-control-allow-origin" not in viewer.headers


def test_host_check_applies_only_while_the_stream_stays_on_this_computer():
    local = FrameStream(CFG)
    assert local.host_allowed("127.0.0.1:8766") and local.host_allowed("localhost:8766")
    assert local.host_allowed("LOCALHOST") and local.host_allowed("127.0.0.1")
    assert not local.host_allowed("attacker.example:8766")
    assert not local.host_allowed("127.0.0.1.attacker.example")
    assert not local.host_allowed(None) and not local.host_allowed("")
    shared = FrameStream(dataclasses.replace(CFG, host="0.0.0.0"))
    assert shared.host_allowed("paul-laptop:8766")


def test_a_port_that_is_taken_is_an_error_at_start():
    with FrameStream(CFG) as first:
        assert first.port != 0 and first.url == f"http://127.0.0.1:{first.port}/stream"
        with pytest.raises(OSError):
            FrameStream(dataclasses.replace(CFG, port=first.port)).start()


def test_a_viewer_that_leaves_is_forgotten():
    with FrameStream(CFG) as stream:
        with Viewer(stream) as viewer:
            wait_for(lambda: stream.viewers == 1)
            stream.submit(image(100))
            viewer.picture()
        # The server learns of it when a write fails, so frames must keep coming.
        wait_for(lambda: stream.viewers == 0, doing=lambda: stream.submit(image(100)))


def test_closing_ends_every_viewer_and_can_be_repeated():
    stream = FrameStream(CFG).start()
    with Viewer(stream) as viewer:
        wait_for(lambda: stream.viewers == 1)
        stream.close()
        with pytest.raises((EOFError, ConnectionError)):
            viewer.picture(timeout=2.0)
    stream.close()
    stream.submit(image(1))  # harmless after closing
