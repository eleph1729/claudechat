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
# make_fixture.py videos longer than 60 s
LONG_FIXTURE_VIDEOS = [1, 4, 9, 22, 27]


def fake(**kw):
    req = urllib.request.Request("http://127.0.0.1:8765/control", data=json.dumps(kw).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def start_server(*extra):
    env = {**os.environ, "ANTHROPIC_BASE_URL": "http://127.0.0.1:8765", "ANTHROPIC_API_KEY": "fake"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                             "--seed", "3", "--countdown", str(COUNTDOWN), *extra], env=env, cwd=ROOT)
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/api/state")
            return proc
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def state(page):
    return page.evaluate("fetch('/api/state').then(r => r.json())")


def wait_ready(page, number=None):
    """The current question once it has loaded (and, if given, once it is question `number`)."""
    for _ in range(100):
        q = state(page)["question"]
        if q and q["status"] == "ready" and number in (None, q["number"]):
            return q
        time.sleep(0.1)
    raise AssertionError("question never loaded")


def downloads():
    """Media files fully downloaded: un-ranged GETs from the "CDN". (Length checks are
    a HEAD on the Hub to find the CDN URL, then ffprobe's ranged GETs.)"""
    out = set()
    for line in HUB_LOG.read_text().splitlines():
        method, path, *rest = line.split()
        if method == "GET" and path.startswith("/cdn/media/") and rest == ["cdn"]:
            out.add(path[len("/cdn/"):])
    return out


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

            # Walk through a dozen random questions: no video over a minute ever shows.
            image_done = video_done = False
            for n in range(1, 13):
                page.keyboard.press("Space")
                q = wait_ready(page, n)
                seen.append(q["task_id"])
                assert q["claude_status"] in ("pending", "done"), "Claude didn't start with the question"
                time.sleep(0.5)
                assert len(downloads()) >= len(seen) + 1, "next question wasn't prefetched"
                if q["kind"] == "video":
                    assert q["duration"] <= 60, f"{q['task_id']} is {q['duration']} s long"
                    expect(page.locator("#len")).to_have_text(
                        f"▶ video {int(q['duration'] // 60)}:{int(round(q['duration'] % 60)):02d}")
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
                elif q["kind"] == "video" and not video_done:
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
                    expect(page.locator(".ref-card")).to_be_visible()
                    page.screenshot(path=SHOTS / "04_video_revealed.png")
                    video_done = True
                elif q["kind"] == "video":
                    page.keyboard.press("v")              # V skips the countdown
                    expect(page.locator("#cd")).to_be_hidden(timeout=1500)
            assert image_done and video_done, "never drew both an image and a video"
            page.keyboard.press("s")
            seen.append(wait_ready(page, 13)["task_id"])
            page.keyboard.press("ArrowLeft")
            expect(page.locator("#awards")).to_contain_text("skipped")
            page.keyboard.press("ArrowRight")
            wait_ready(page, 13)

            # A failed call shows the error; C again retries.
            fake(fail=True)
            page.keyboard.press("Space")
            q = wait_ready(page, 14)
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
            seen.append(wait_ready(page, 15)["task_id"])
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
    # Videos only: every one of the fixture's 25 short-enough videos, and none longer.
    proc = start_server("--kind", "video")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/")
            shown = []
            for n in range(1, 26):
                page.keyboard.press("Space")
                q = wait_ready(page, n)
                assert q["kind"] == "video" and q["duration"] <= 60, q
                shown.append(q["task_id"])
            assert len(set(shown)) == 25, f"a video repeated before all short ones were shown: {shown}"
            browser.close()
    finally:
        proc.terminate()
        proc.wait()

    assert not errors, errors
    long_ones = {f"media/videos/vid_{i:03d}.mp4" for i in LONG_FIXTURE_VIDEOS}
    assert not downloads() & long_ones, f"downloaded long videos: {downloads() & long_ones}"
    durations = json.loads((Path(os.environ["HSS_DATA_DIR"]) / "durations.json").read_text())
    assert sum(d > 60 for d in durations.values()) == len(LONG_FIXTURE_VIDEOS), durations
    print(f"UI end-to-end: all checks passed ({len(seen)} questions shown)")


if __name__ == "__main__":
    main()
