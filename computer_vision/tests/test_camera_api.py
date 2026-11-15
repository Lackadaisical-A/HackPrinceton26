"""Camera API over real HTTP sockets, including browser CORS and input validation."""
import json
import socket
import socketserver
import time
from http.client import HTTPConnection

import pytest

from paul_vision import stream as stream_module
from paul_vision.config import StreamConfig
from paul_vision.stream import FrameStream

ORIGIN = "http://127.0.0.1:5173"
CFG = StreamConfig(enabled=True, host="127.0.0.1", port=0, max_width=64, jpeg_quality=80, max_fps=30)


class Control:
    refreshed = 0
    selected = None

    def refresh(self):
        self.refreshed += 1

    def status(self):
        return {"sources": [], "active_id": self.selected}

    def select(self, camera_id):
        if camera_id != "usb":
            raise ValueError("unknown camera")
        self.selected = camera_id


@pytest.fixture
def service():
    control = Control()
    with FrameStream(CFG, camera_control=control) as stream:
        yield stream, control


def request(stream, method, path, body=None, origin=ORIGIN, **headers):
    if origin is not None:
        headers["Origin"] = origin
    headers.setdefault("Content-Type", "application/json")
    conn = HTTPConnection("127.0.0.1", stream.port, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_discovery_refresh_preflight_and_selection(service):
    stream, control = service
    status, headers, body = request(stream, "GET", "/sources?refresh=1")
    assert status == 200 and control.refreshed == 1
    assert json.loads(body)["sources"] == []
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Cache-Control"] == "no-store"
    status, headers, _ = request(stream, "OPTIONS", "/source")
    assert status == 204
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert headers["Access-Control-Allow-Headers"] == "Content-Type"
    status, _, body = request(stream, "POST", "/source", '{"id":"usb"}')
    assert status == 202 and control.selected == "usb"
    assert json.loads(body)["active_id"] == "usb"


@pytest.mark.parametrize("origin", [None, "null", "https://example.com", "http://localhost.evil.test:5173"])
def test_foreign_or_missing_origin_cannot_change_camera(service, origin):
    stream, control = service
    status, headers, _ = request(stream, "POST", "/source", '{"id":"usb"}', origin=origin)
    assert status == 403 and control.selected is None
    assert "Access-Control-Allow-Origin" not in headers


def test_foreign_host_and_origin_cannot_read_devices(service):
    stream, _ = service
    assert request(stream, "GET", "/sources", Host="evil.test")[0] == 403
    assert request(stream, "GET", "/sources", origin="https://evil.test")[0] == 403
    assert request(stream, "GET", "/sources", origin=None)[0] == 200


@pytest.mark.parametrize("body,content_type", [('{"id":"missing"}', "application/json"),
    ('{}', "application/json"), ('[]', "application/json"), ('bad json', "application/json"),
    ('x' * 1025, "application/json"), ('{"id":"usb"}', "text/plain")])
def test_invalid_selection_cannot_change_camera(service, body, content_type):
    stream, control = service
    assert request(stream, "POST", "/source", body, **{"Content-Type": content_type})[0] == 400
    assert control.selected is None


def test_no_controller_reports_unavailable():
    with FrameStream(CFG) as stream:
        assert request(stream, "GET", "/sources")[0] == 503


def head(stream, method, path, length, origin=ORIGIN, content_type="application/json"):
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{stream.port}", f"Content-Type: {content_type}",
             f"Content-Length: {length}"] + ([f"Origin: {origin}"] if origin else [])
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def read_to_end(sock):
    answer = b""
    while chunk := sock.recv(4096):
        answer += chunk
    return answer


def send_body_late(stream, method, path, body, pause_s=0.05, **head_args):
    """The headers, then the body `pause_s` later, then the whole answer: the body reaches the server after
    it has answered, as it can from any client. Returns the status."""
    with socket.create_connection(("127.0.0.1", stream.port), timeout=3) as sock:
        sock.sendall(head(stream, method, path, len(body), **head_args))
        time.sleep(pause_s)
        sock.sendall(body.encode())
        return int(read_to_end(sock).split()[1])


# Every way a request is answered without its body being read. The server used to close the connection
# with the body unread, which resets it; on Windows that threw the answer away (WinError 10053).
@pytest.mark.parametrize("method,path,body,head_args,status", [
    ("POST", "/source", '{"id":"usb"}', {"origin": "https://example.com"}, 403),
    ("POST", "/source", '{"id":"usb"}', {"origin": None}, 403),
    ("POST", "/source", '{"id":"usb"}', {"content_type": "text/plain"}, 400),
    ("POST", "/source", "x" * 1025, {}, 400),
    ("POST", "/elsewhere", '{"id":"usb"}', {}, 404),
    ("GET", "/sources", '{"id":"usb"}', {"origin": "https://evil.test"}, 403),
    ("OPTIONS", "/source", '{"id":"usb"}', {"origin": "https://evil.test"}, 403),
], ids=["foreign origin", "no origin", "text/plain", "too long", "unknown path", "GET foreign", "OPTIONS foreign"])
def test_a_refused_request_gets_its_answer_when_its_body_comes_late(service, method, path, body, head_args, status):
    stream, control = service
    for _ in range(3):
        assert send_body_late(stream, method, path, body, **head_args) == status
    assert control.selected is None


def test_unavailable_selection_gets_its_answer_when_the_body_comes_late():
    with FrameStream(CFG) as stream:
        assert send_body_late(stream, "POST", "/source", '{"id":"usb"}') == 503


def test_a_client_that_withholds_or_floods_the_body_does_not_hold_the_server(service, monkeypatch):
    monkeypatch.setattr(stream_module, "_LINGER_S", 0.5)
    closed = []

    def close_request(server, request):
        closed.append(time.perf_counter())
        socketserver.TCPServer.close_request(server, request)

    def let_go_after(started):
        while not closed and time.perf_counter() < started + 5.0:
            time.sleep(0.01)
        assert closed, "the server never closed the connection"
        return closed.pop() - started

    monkeypatch.setattr(stream_module._Server, "close_request", close_request)
    stream, control = service
    # Declares a huge body, sends none of it and never closes: answered at once, let go after _LINGER_S.
    with socket.create_connection(("127.0.0.1", stream.port), timeout=3) as sock:
        started = time.perf_counter()
        sock.sendall(head(stream, "POST", "/source", 10**9, origin="https://example.com"))
        assert read_to_end(sock).split()[1] == b"403"
        assert time.perf_counter() - started < 0.4
        assert 0.4 < let_go_after(started) < 3.0
    # Sends far more than _LINGER_MAX_BYTES: the server stops reading and lets go without waiting.
    with socket.create_connection(("127.0.0.1", stream.port), timeout=3) as sock:
        started = time.perf_counter()
        sock.sendall(head(stream, "POST", "/source", 2**24, origin="https://example.com"))
        try:
            sock.sendall(b"x" * 2**24)
        except OSError:
            pass  # reset once the server let go
        assert let_go_after(started) < 0.4
    assert request(stream, "POST", "/source", '{"id":"usb"}')[0] == 202 and control.selected == "usb"
