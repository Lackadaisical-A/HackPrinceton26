"""Camera API over real HTTP sockets, including browser CORS and input validation."""
import json
from http.client import HTTPConnection

import pytest

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
