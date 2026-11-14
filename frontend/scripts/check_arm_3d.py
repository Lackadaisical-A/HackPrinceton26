# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.51"]
# ///
"""Headless Chrome checks against the built console; never opens robot hardware.

Run after npm run build: uv run frontend/scripts/check_arm_3d.py
Requires an installed Google Chrome. Screenshots go to runs/arm-3d/.
"""
from contextlib import closing
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
OUTPUT = ROOT / "runs" / "arm-3d"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-url", help="Also verify model assets on an already-running Vite dev server")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (OUTPUT / "preview.log").open("w") as log:
        server = subprocess.Popen([shutil.which("node"), "node_modules/vite/bin/vite.js", "preview",
                                   "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                                  cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(50):
                try:
                    with urlopen(url, timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
                errors = []
                failed_assets = []
                connections = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: failed_assets.append(response.url)
                        if response.status >= 400 and ".stl" in response.url else None)
                page.route_web_socket("**/arm-test", lambda ws: connections.append(ws))
                page.goto(url + "/?source=live&wsUrl=ws://127.0.0.1:9999/arm-test", wait_until="networkidle")
                model = page.locator('[data-arm-model="ready"]')
                model.wait_for(timeout=30000)
                assert connections, "test telemetry WebSocket was not opened"
                page.get_by_text("Waiting for arm data · reference pose", exact=True).wait_for()

                def send_pose(pos, preview=False):
                    connections[-1].send("robot " + json.dumps({
                        "t": time.time(), "pos": pos, "load": None if preview else [0] * 6,
                        "control": {"input": "potentiometer", "enabled": not preview,
                                    "input_fresh": True, "feedback": "preview" if preview else "measured",
                                    "target": pos},
                    }))

                canvas = model.locator("canvas")
                send_pose([0, -20, 35, 0, 0, 25])
                page.get_by_text("Live measured pose", exact=True).wait_for()
                pose_a = canvas.screenshot(path=str(OUTPUT / "pose-a.png"))
                send_pose([55, 25, -20, 35, 50, 90])
                page.wait_for_timeout(150)
                pose_b = canvas.screenshot(path=str(OUTPUT / "pose-b.png"))
                assert pose_a != pose_b, "live telemetry did not articulate the model"
                page.get_by_text("Arm data lost · showing last pose", exact=True).wait_for(timeout=4000)
                assert canvas.screenshot() == pose_b, "stale input should hold the last pose"
                rect = canvas.bounding_box()
                page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
                page.mouse.down()
                page.mouse.move(rect["x"] + rect["width"] / 2 + 80, rect["y"] + rect["height"] / 2 + 20, steps=10)
                page.mouse.up()
                page.wait_for_timeout(800)
                assert canvas.screenshot() != pose_b, "orbit gesture did not change the view"
                page.get_by_role("button", name="Reset view", exact=True).click()
                page.wait_for_timeout(800)
                assert canvas.screenshot() == pose_b, "reset did not restore the camera"
                send_pose([0, -20, 35, 0, 0, 25], preview=True)
                page.get_by_text("Pot target preview · no robot connected", exact=True).wait_for()
                model.screenshot(path=str(OUTPUT / "viewer.png"))
                page.screenshot(path=str(OUTPUT / "console.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                model.scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                model.screenshot(path=str(OUTPUT / "mobile.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile view overflows"
                assert not errors, errors
                assert not failed_assets, failed_assets
                page.close()

                # Missing meshes must produce an explicit fallback, not a partial arm.
                broken = browser.new_page()
                broken.route("**/*.stl", lambda route: route.abort())
                broken.goto(url)
                broken.locator('[data-arm-model="error"]').wait_for(timeout=30000)
                broken.get_by_text("Could not load the SO-101 model.", exact=False).wait_for()
                assert broken.get_by_role("columnheader", name="Joint", exact=True).is_visible()
                broken.close()
                if args.dev_url:
                    dev = browser.new_page()
                    dev.on("pageerror", lambda error: errors.append(str(error)))
                    dev.goto(args.dev_url)
                    dev.locator('[data-arm-model="ready"]').wait_for(timeout=30000)
                    assert not errors, errors
                    dev.close()
                browser.close()
                print("PASS: production assets, 3D articulation, stale hold, orbit/reset, dry-run labels, mobile layout, missing-mesh fallback")
                print(f"Screenshots: {OUTPUT}")
        finally:
            server.terminate()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
