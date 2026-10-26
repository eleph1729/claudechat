"""Drive the projector and camera pages in headless Chromium against the fake API.

    python tests/e2e_ui.py <data_dir> <screenshot_dir>

Expects tests/fake_anthropic.py on 127.0.0.1:8765 and a data dir prepared
with prepare.py. Starts server.py itself (live, then --offline).
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DATA, SHOTS = Path(sys.argv[1]), Path(sys.argv[2])
SHOTS.mkdir(parents=True, exist_ok=True)
PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"


def fake(**kw):
    req = urllib.request.Request("http://127.0.0.1:8765/control", data=json.dumps(kw).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def start_server(*extra):
    env = {**os.environ, "HSS_DATA_DIR": str(DATA), "ANTHROPIC_BASE_URL": "http://127.0.0.1:8765",
           "ANTHROPIC_API_KEY": "fake"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT), *extra],
                            env=env, cwd=ROOT)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/state")
            return proc
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def handwriting(path: Path):
    im = Image.new("RGB", (1200, 800), "#fbfaf4")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 80)
    except OSError:
        font = None
    d.text((80, 300), "There are 4 circles", fill="#1a237e", font=font)
    im.save(path, "JPEG")


def state(page):
    return page.evaluate("fetch('/api/state').then(r => r.json())")


def main():
    photo = SHOTS / "handwriting.jpg"
    handwriting(photo)
    fake(delay=0, fail=False)
    proc = start_server("--live-grace", "3")
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/")
            expect(page.locator("#title")).to_be_visible()
            page.screenshot(path=SHOTS / "01_title.png")

            # Q1: question appears, live call starts in the background
            page.keyboard.press("Space")
            expect(page.locator("#quiz")).to_be_visible()
            expect(page.locator("#prompt")).not_to_be_empty()
            time.sleep(1.5)
            page.keyboard.press("p")
            expect(page.locator("#pens")).to_be_visible()
            page.screenshot(path=SHOTS / "02_question_pens_down.png")
            page.keyboard.press("c")
            expect(page.locator(".badge.live")).to_be_visible(timeout=10000)
            page.keyboard.press("t")
            expect(page.locator(".thinking")).to_be_visible()
            page.keyboard.press("t")
            page.keyboard.press("r")
            expect(page.locator(".ref-card")).to_be_visible()
            expect(page.locator(".mark.y, .mark.n").first).to_be_visible(timeout=10000)
            page.keyboard.press("a")
            expect(page.locator("#sa")).to_have_text("1")
            page.screenshot(path=SHOTS / "03_claude_live_and_rubric.png")
            s = state(page)
            assert s["question"]["source"] == "live", s["question"]["source"]
            assert s["question"]["judgment"] is not None

            # Q2 (a video): phone submits a handwritten answer
            page.keyboard.press("Space")
            expect(page.locator("#media video")).to_be_visible()
            assert state(page)["question"]["kind"] == "video"
            phone = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            phone.on("pageerror", lambda e: errors.append(str(e)))
            phone.goto(BASE + "/camera")
            expect(phone.locator("#qlabel")).to_have_text("Question 2 of 35")
            phone.fill("#name", "Table 3")
            phone.set_input_files("#file", str(photo))
            expect(phone.locator("#previewCard")).to_be_visible()
            phone.screenshot(path=SHOTS / "04_phone_preview.png")
            phone.click("#send")
            expect(phone.locator("#result")).to_contain_text("Graded", timeout=15000)
            expect(phone.locator("#result")).to_contain_text("There are 4 circles")
            phone.screenshot(path=SHOTS / "05_phone_graded_hidden.png")
            expect(page.locator("#cam")).to_contain_text("1 photo answer")
            page.keyboard.press("h")
            page.keyboard.press("c")
            expect(page.locator(".badge.live")).to_be_visible(timeout=10000)
            page.keyboard.press("r")
            expect(page.locator(".sub")).to_contain_text("Table 3")
            expect(page.locator(".sub .badge")).to_be_visible()
            page.screenshot(path=SHOTS / "06_video_with_camera_answer.png")
            expect(phone.locator("#result .pass, #result .fail")).to_be_visible(timeout=5000)
            phone.screenshot(path=SHOTS / "07_phone_revealed.png")

            # Q3: slow live call -> waiting, then cached after the 3 s grace
            fake(delay=20)
            page.keyboard.press("Space")
            page.keyboard.press("c")
            expect(page.locator(".badge.wait")).to_be_visible()
            page.screenshot(path=SHOTS / "08_waiting_for_live.png")
            expect(page.locator(".badge.cached")).to_be_visible(timeout=8000)
            expect(page.locator(".note")).to_contain_text("still running")
            page.screenshot(path=SHOTS / "09_cached_fallback.png")

            # Q4: slow again, second C forces the cache right away
            page.keyboard.press("Space")
            page.keyboard.press("c")
            expect(page.locator(".badge.wait")).to_be_visible()
            page.keyboard.press("c")
            expect(page.locator(".badge.cached")).to_be_visible(timeout=2000)

            # Q5: failing live call -> cached with the error note
            fake(delay=0, fail=True)
            page.keyboard.press("Space")
            time.sleep(1.5)
            page.keyboard.press("c")
            expect(page.locator(".badge.cached")).to_be_visible(timeout=5000)
            expect(page.locator(".note")).to_contain_text("Live call failed")
            fake(fail=False)

            # S skips Q6; back/forward keeps per-question state
            page.keyboard.press("Space")
            page.keyboard.press("s")
            s = state(page)
            assert s["index"] == 6, s["index"]
            page.keyboard.press("ArrowLeft")
            expect(page.locator("#awards")).to_contain_text("skipped")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            expect(page.locator("#awards")).to_contain_text("+1 Humans")
            for _ in range(40):
                page.keyboard.press("Space")
            expect(page.locator("#end")).to_be_visible()
            expect(page.locator("#end-h")).to_have_text("1")
            expect(page.locator("#end-a")).to_have_text("1")
            page.screenshot(path=SHOTS / "10_final.png")
            page.keyboard.press("?")
            browser.close()
    finally:
        proc.terminate()
        proc.wait()

    # Offline: cached answers only, no API traffic
    log = Path(os.environ["FAKE_LOG"])
    before = len(log.read_text().splitlines())
    proc = start_server("--offline")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/")
            page.keyboard.press("Space")
            page.keyboard.press("Space")
            expect(page.locator("#offline-chip")).to_be_visible()
            page.keyboard.press("c")
            expect(page.locator(".badge.cached")).to_be_visible(timeout=2000)
            page.keyboard.press("r")
            expect(page.locator(".mark.y, .mark.n").first).to_be_visible()
            page.screenshot(path=SHOTS / "11_offline_cached.png")
            phone = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            phone.goto(BASE + "/camera")
            expect(phone.locator("#q")).to_contain_text("offline")
            browser.close()
    finally:
        proc.terminate()
        proc.wait()
    after = len(log.read_text().splitlines())
    assert after == before, f"offline mode made {after - before} API calls"
    assert not errors, errors
    print("UI end-to-end: all checks passed")


if __name__ == "__main__":
    main()
