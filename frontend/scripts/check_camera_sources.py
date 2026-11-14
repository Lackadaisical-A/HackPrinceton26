# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.51"]
# ///
"""Check camera selection in installed headless Chrome against a running frontend.

uv run frontend/scripts/check_camera_sources.py --url http://127.0.0.1:5173
Add --live-stream http://127.0.0.1:18766/stream to switch both attached cameras,
then restore the Innomaker robot camera. No robot hardware is opened.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

OUTPUT = Path(__file__).resolve().parents[2] / "runs" / "camera-selector"


def check_mocked(browser, url):
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route_web_socket("**/camera-test", lambda ws: None)
    devices = [{"id": "usb", "index": 0, "name": "Robot USB"},
               {"id": "integrated", "index": 1, "name": "Integrated Camera"}]
    state = {"sources": devices, "active_id": "usb", "active_name": "Robot USB",
             "pending_id": None, "generation": 0, "error": None, "can_switch": True}
    polls = 0
    fail = False
    unavailable = False

    def api(route):
        nonlocal polls
        if unavailable:
            route.fulfill(status=503, json={"error": "Camera selection is unavailable."})
            return
        if route.request.method == "POST":
            choice = route.request.post_data_json["id"]
            if fail:
                state["error"] = "Could not open Robot USB. Kept the previous source."
            else:
                state["pending_id"] = choice
                state["error"] = None
                polls = 0
        elif state["pending_id"]:
            polls += 1
            if polls >= 2:
                state["active_id"] = state["pending_id"]
                state["active_name"] = next(item["name"] for item in devices if item["id"] == state["active_id"])
                state["pending_id"] = None
                state["generation"] += 1
        route.fulfill(status=202 if route.request.method == "POST" else 200, json=state)

    page.route("http://127.0.0.1:18767/sources*", api)
    page.route("http://127.0.0.1:18767/source", api)
    page.route("http://127.0.0.1:18767/stream*", lambda route: route.abort())
    query = urlencode({"source": "live", "wsUrl": "ws://127.0.0.1:9999/camera-test",
                       "cameraUrl": "http://127.0.0.1:18767/stream"})
    page.goto(f"{url}/?{query}", wait_until="domcontentloaded")
    select = page.get_by_role("combobox", name="Camera source")
    refresh = page.get_by_role("button", name="Refresh camera sources")
    expect(select).to_be_enabled()
    expect(select).to_have_value("usb")
    expect(select.locator("option")).to_have_count(2)
    select.select_option("integrated")
    expect(page.get_by_text("Switching to Integrated Camera…", exact=True)).to_be_visible()
    expect(select).to_be_disabled()
    expect(select).to_have_value("usb")
    expect(select).to_have_value("integrated", timeout=5000)
    expect(select).to_be_enabled()
    fail = True
    select.select_option("usb")
    expect(page.get_by_text("Could not open Robot USB. Kept the previous source.", exact=True)).to_be_visible()
    expect(select).to_have_value("integrated")
    state["sources"] = [devices[1]]
    refresh.click()
    expect(select.locator("option")).to_have_count(1)
    state["can_switch"] = False
    state["error"] = None
    refresh.click()
    expect(select).to_be_disabled()
    expect(page.get_by_text("Camera switching is unavailable while video recording is active.")).to_be_visible()
    unavailable = True
    refresh.click()
    expect(page.get_by_text("Camera selection is unavailable.", exact=True)).to_be_visible()
    expect(select).to_be_disabled()
    unavailable = False
    state["can_switch"] = True
    state["sources"] = devices
    refresh.click()
    expect(select).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile layout overflows"
    assert not errors, errors
    page.close()
    print("PASS: selection, pending state, failure retention, refresh, recording, API recovery, mobile layout")


def check_live(browser, url, stream):
    parsed = urlsplit(stream)
    base = f"{parsed.scheme}://{parsed.netloc}"
    with urlopen(base + "/sources?refresh=1", timeout=5) as response:
        initial = json.load(response)
    robot = next(item for item in initial["sources"] if "innomaker" in item["name"].lower())
    other = next(item for item in initial["sources"] if "integrated" in item["name"].lower())
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    query = urlencode({"source": "live", "cameraUrl": stream})
    page.goto(f"{url}/?{query}", wait_until="domcontentloaded")
    select = page.get_by_role("combobox", name="Camera source")
    try:
        expect(select).to_be_enabled(timeout=15000)
        select.select_option(other["id"])
        expect(select).to_have_value(other["id"], timeout=20000)
        expect(select).to_be_enabled()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUTPUT / "integrated-camera.png"), full_page=True)
        select.select_option(robot["id"])
        expect(select).to_have_value(robot["id"], timeout=20000)
        expect(select).to_be_enabled()
        page.wait_for_function("document.querySelector('img[alt=\"Live view from the wrist camera\"]')?.naturalWidth > 0")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUTPUT / "robot-camera.png"), full_page=True)
        assert not errors, errors
        with urlopen(base + "/sources", timeout=5) as response:
            final = json.load(response)
        assert final["active_id"] == robot["id"] and not final["pending_id"] and not final["error"], final
        print("PASS: real browser CORS, switched to Integrated Camera and back to " + robot["name"])
    finally:
        # Restore the robot feed even if a browser assertion fails mid-switch.
        with urlopen(base + "/sources", timeout=5) as response:
            current = json.load(response)
        if current["active_id"] != robot["id"] and not current["pending_id"]:
            request = Request(base + "/source", data=json.dumps({"id": robot["id"]}).encode(),
                              headers={"Content-Type": "application/json", "Origin": url}, method="POST")
            with urlopen(request, timeout=5):
                pass
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--live-stream")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        check_mocked(browser, args.url.rstrip("/"))
        if args.live_stream:
            check_live(browser, args.url.rstrip("/"), args.live_stream)
        browser.close()


if __name__ == "__main__":
    main()
