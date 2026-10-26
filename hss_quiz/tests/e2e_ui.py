"""Drive the projector page in headless Chromium against the fake Hub and API.

    python tests/e2e_ui.py <screenshot_dir> <hub_requests.log>

Expects tests/fake_anthropic.py on 127.0.0.1:8765 and tests/fake_hub.py on
127.0.0.1:8766 (see run_e2e.sh). Starts server.py itself.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS, HUB_LOG = Path(sys.argv[1]), Path(sys.argv[2])
SHOTS.mkdir(parents=True, exist_ok=True)
PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
COUNTDOWN = 4


def fake(**kw):
    req = urllib.request.Request("http://127.0.0.1:8765/control", data=json.dumps(kw).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def start_server():
    env = {**os.environ, "ANTHROPIC_BASE_URL": "http://127.0.0.1:8765", "ANTHROPIC_API_KEY": "fake"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                             "--seed", "3", "--countdown", str(COUNTDOWN)], env=env, cwd=ROOT)
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/api/state")
            return proc
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def state(page):
    return page.evaluate("fetch('/api/state').then(r => r.json())")


def wait_ready(page):
    for _ in range(100):
        q = state(page)["question"]
        if q and q["status"] == "ready":
            return q
        time.sleep(0.1)
    raise AssertionError("question never loaded")


def media_fetched():
    return {line.split()[1] for line in HUB_LOG.read_text().splitlines() if "/resolve/main/media/" in line}


def main():
    fake(delay=0, fail=False)
    proc = start_server()
    errors = []
    seen = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/")
            expect(page.locator("#title")).to_be_visible()
            expect(page.locator("#title-src")).to_contain_text("70 tasks")
            page.screenshot(path=SHOTS / "01_title.png")

            # Walk forward until we've covered an image, a short video and a long one.
            image_done = video_done = long_done = False
            while not (image_done and video_done and long_done):
                page.keyboard.press("Space")
                q = wait_ready(page)
                seen.append(q["task_id"])
                assert len(seen) < 40, "never met every kind of question"
                assert q["claude_status"] in ("pending", "done"), "Claude didn't start with the question"
                time.sleep(0.5)
                assert len(media_fetched()) >= len(seen) + 1, "next question wasn't prefetched"
                if q["kind"] == "image" and not image_done:
                    expect(page.locator("#media img")).to_be_visible()
                    expect(page.locator("#len")).to_be_hidden()
                    page.keyboard.press("p")
                    expect(page.locator("#pens")).to_be_visible()
                    page.keyboard.press("c")
                    expect(page.locator(".claude-card .body")).to_be_visible(timeout=10000)
                    page.keyboard.press("r")
                    expect(page.locator(".mark.y, .mark.n").first).to_be_visible(timeout=10000)
                    page.keyboard.press("a")
                    expect(page.locator("#sa")).to_have_text("1")
                    page.screenshot(path=SHOTS / "02_image_revealed.png")
                    image_done = True
                elif q["kind"] == "video" and q["duration"] <= 60 and not video_done:
                    expect(page.locator("#len")).to_have_text(
                        f"▶ video {int(q['duration'] // 60)}:{int(round(q['duration'] % 60)):02d}")
                    expect(page.locator("#len")).not_to_have_class("len long")
                    expect(page.locator("#cd")).to_be_visible()
                    page.screenshot(path=SHOTS / "03_video_countdown.png")
                    time.sleep(1)
                    assert page.evaluate("document.getElementById('vid').paused"), "video played during countdown"
                    expect(page.locator("#cd")).to_be_hidden(timeout=(COUNTDOWN + 2) * 1000)
                    page.wait_for_function("!document.getElementById('vid').paused", timeout=5000)
                    page.keyboard.press("h")
                    page.keyboard.press("c")
                    expect(page.locator(".claude-card .body")).to_be_visible(timeout=10000)
                    page.keyboard.press("r")
                    page.screenshot(path=SHOTS / "04_video_revealed.png")
                    video_done = True
                elif q["kind"] == "video" and q["duration"] > 60 and not long_done:
                    expect(page.locator("#len.long")).to_be_visible()
                    expect(page.locator("#cd")).to_contain_text("press S to skip")
                    page.screenshot(path=SHOTS / "05_long_video_countdown.png")
                    page.keyboard.press("s")
                    assert state(page)["question"]["number"] == len(seen) + 1
                    page.keyboard.press("ArrowLeft")
                    expect(page.locator("#awards")).to_contain_text("skipped")
                    page.keyboard.press("ArrowRight")
                    long_done = True
                elif q["kind"] == "video":
                    # V skips the countdown
                    page.keyboard.press("v")
                    expect(page.locator("#cd")).to_be_hidden(timeout=1500)

            # A failed call shows the error; C again retries.
            fake(fail=True)
            page.keyboard.press("Space")
            q = wait_ready(page)
            seen.append(q["task_id"])
            time.sleep(0.5)
            page.keyboard.press("c")
            expect(page.locator(".claude-card .err")).to_contain_text("fake failure", timeout=5000)
            page.screenshot(path=SHOTS / "06_claude_error.png")
            fake(fail=False)
            page.keyboard.press("c")
            expect(page.locator(".claude-card .body")).to_be_visible(timeout=10000)

            # A slow call shows Claude still thinking.
            fake(delay=3)
            page.keyboard.press("Space")
            seen.append(wait_ready(page)["task_id"])
            page.keyboard.press("c")
            expect(page.locator(".badge.wait")).to_be_visible()
            expect(page.locator(".claude-card .body")).to_be_visible(timeout=10000)
            fake(delay=0)

            assert len(set(seen)) == len(seen), "a question repeated"
            page.keyboard.press("?")
            expect(page.locator("#help")).to_be_visible()
            browser.close()
    finally:
        proc.terminate()
        proc.wait()
    assert not errors, errors
    print(f"UI end-to-end: all checks passed ({len(seen)} questions shown)")


if __name__ == "__main__":
    main()
