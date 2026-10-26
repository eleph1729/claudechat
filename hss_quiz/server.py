"""HSS quiz show server: projector page at /, phone answer page at /camera.

    python server.py              # live Claude calls, cached answers as fallback
    python server.py --offline    # cached answers only, no network calls

The show's state lives here, so the projector page, the host's keyboard and
the phones all see the same thing; the pages poll /api/state.
"""

from __future__ import annotations

import argparse
import io
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import hss_common as hc

STATIC = hc.ROOT / "static"
SHORTLIST = hc.DATA_DIR / "shortlist.json"
CACHE = hc.DATA_DIR / "cache" / "answers.json"
FRAMES = hc.DATA_DIR / "frames"
UPLOADS = hc.DATA_DIR / "uploads"


class Show:
    def __init__(self, tasks: List[hc.Task], cache: Dict[str, Any], opts: argparse.Namespace):
        self.tasks = tasks
        self.cache = cache
        self.opts = opts
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.index = -1                   # -1 = title screen, len(tasks) = final screen
        self.q: Dict[int, Dict[str, Any]] = {}
        self.live: Dict[str, Dict[str, Any]] = {}
        self.camera: Dict[str, List[Dict[str, Any]]] = {}

    # ---- per-question UI state -------------------------------------------
    def qstate(self, i: int) -> Dict[str, Any]:
        return self.q.setdefault(i, {
            "shown_at": None, "pens_down_at": None, "claude": False, "claude_at": None,
            "force_cached": False, "source": None, "ref": False,
            "human": False, "ai": False, "skipped": False})

    def go(self, i: int) -> None:
        self.index = max(-1, min(i, len(self.tasks)))
        if 0 <= self.index < len(self.tasks):
            st = self.qstate(self.index)
            if st["shown_at"] is None:
                st["shown_at"] = time.time()
            self.start_live(self.tasks[self.index])

    def action(self, name: str) -> None:
        with self.lock:
            on_question = 0 <= self.index < len(self.tasks)
            st = self.qstate(self.index) if on_question else None
            if name == "next":
                self.go(self.index + 1)
            elif name == "back":
                self.go(self.index - 1)
            elif name == "skip" and st:
                st["skipped"] = True
                st["human"] = st["ai"] = False
                self.go(self.index + 1)
            elif name == "pens" and st:
                st["pens_down_at"] = None if st["pens_down_at"] else time.time()
            elif name == "claude" and st:
                if st["claude"] and st["source"] is None:
                    st["force_cached"] = True     # second C: stop waiting for the live call
                st["claude"] = True
                st["claude_at"] = st["claude_at"] or time.time()
                st["pens_down_at"] = st["pens_down_at"] or time.time()
            elif name == "ref" and st:
                st["ref"] = not st["ref"]
            elif name in ("human", "ai") and st:
                st[name] = not st[name]
                st["skipped"] = False
            elif name == "restart":
                self.q.clear()
                self.camera.clear()
                self.index = -1

    # ---- Claude: live call with cached fallback ---------------------------
    def start_live(self, task: hc.Task) -> None:
        if self.opts.offline or task.task_id in self.live:
            return
        job = {"status": "pending", "started": time.time()}
        self.live[task.task_id] = job

        def run() -> None:
            try:
                out = hc.ask_claude(task, FRAMES, model=self.opts.model, effort=self.opts.effort)
                with self.lock:
                    job.update(out, status="done")
                if not self.opts.no_live_judge:
                    try:
                        verdict = hc.judge(task, out["answer"], model=self.opts.judge_model)
                        with self.lock:
                            job["judgment"] = verdict
                    except Exception as exc:  # noqa: BLE001
                        with self.lock:
                            job["judge_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    job.update(status="error", error=f"{type(exc).__name__}: {exc}")
                print(f"[live] {task.task_id} failed: {exc}")

        self.pool.submit(run)

    def resolve_source(self, task: hc.Task, st: Dict[str, Any]) -> Optional[str]:
        """Which answer the reveal shows: 'live', 'cached', or None while waiting.
        Once chosen it sticks, so the screen never swaps answers mid-reveal."""
        if st["source"]:
            return st["source"]
        live = self.live.get(task.task_id)
        cached = task.task_id in self.cache
        if live and live["status"] == "done":
            choice = "live"
        elif live and live["status"] == "pending" and (not cached or (
                not st["force_cached"] and time.time() - st["claude_at"] < self.opts.live_grace)):
            choice = None
        elif cached:
            choice = "cached"
        else:
            choice = "none"
        st["source"] = choice
        return choice

    # ---- serialisation ----------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            scores = {"human": sum(1 for s in self.q.values() if s["human"]),
                      "ai": sum(1 for s in self.q.values() if s["ai"]),
                      "played": sum(1 for i, s in self.q.items()
                                    if i < len(self.tasks) and s["shown_at"] and not s["skipped"])}
            out: Dict[str, Any] = {
                "index": self.index, "total": len(self.tasks), "scores": scores,
                "offline": self.opts.offline, "camera_url": self.opts.camera_url,
                "model": self.opts.model, "now": time.time(), "question": None}
            if not 0 <= self.index < len(self.tasks):
                return out
            task = self.tasks[self.index]
            st = self.qstate(self.index)
            live = self.live.get(task.task_id)
            q: Dict[str, Any] = {
                "task_id": task.task_id, "prompt": task.prompt, "kind": task.media_kind,
                "media_url": f"/media/{task.local_media}", "domain": task.domain,
                "subdomain": task.subdomain, "credit": task.credit, "duration": task.duration,
                **{k: st[k] for k in ("shown_at", "pens_down_at", "claude", "claude_at",
                                      "ref", "human", "ai", "skipped")},
                "live_status": live["status"] if live else ("offline" if self.opts.offline else None),
                "live_elapsed": round(time.time() - live["started"], 1) if live else None,
                "cached_available": task.task_id in self.cache,
                "camera_count": len(self.camera.get(task.task_id, [])),
            }
            if st["claude"]:
                source = self.resolve_source(task, st)
                q["source"] = source
                entry = live if source == "live" else self.cache.get(task.task_id) if source == "cached" else None
                if entry:
                    q["answer"] = entry["answer"]
                    q["thinking"] = entry.get("thinking", "")
                    q["answer_meta"] = {k: entry.get(k) for k in ("model", "effort", "latency_s", "created")}
                    q["judgment"] = entry.get("judgment")
                    q["judge_pending"] = source == "live" and "judgment" not in entry and \
                        "judge_error" not in entry and not self.opts.no_live_judge
                if source in ("cached", "none") and live and live["status"] == "error":
                    q["live_error"] = live["error"]
                if source == "none":
                    q["answer_error"] = (live or {}).get("error") or \
                        "No cached answer for this question (run prepare.py)."
                q["live_grace_left"] = max(0.0, self.opts.live_grace - (time.time() - st["claude_at"]))
            if st["ref"]:
                q["reference"] = task.golden_response
                q["criteria"] = task.criteria
                q["camera"] = self.camera.get(task.task_id, [])
            out["question"] = q
            return out


app = FastAPI()
show: Show  # set in main()


class Action(BaseModel):
    action: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/camera")
def camera_page() -> FileResponse:
    return FileResponse(STATIC / "camera.html")


@app.get("/api/state")
def state() -> JSONResponse:
    return JSONResponse(show.snapshot(), headers={"Cache-Control": "no-store"})


@app.post("/api/action")
def act(body: Action) -> JSONResponse:
    allowed = {"next", "back", "skip", "pens", "claude", "ref", "human", "ai", "restart"}
    if body.action not in allowed:
        raise HTTPException(400, f"unknown action {body.action!r}")
    show.action(body.action)
    return state()


@app.get("/api/camera/question")
def camera_question() -> Dict[str, Any]:
    with show.lock:
        if not 0 <= show.index < len(show.tasks):
            return {"index": show.index, "task_id": None, "offline": show.opts.offline}
        t = show.tasks[show.index]
        return {"index": show.index, "task_id": t.task_id, "prompt": t.prompt,
                "total": len(show.tasks), "offline": show.opts.offline}


@app.post("/api/camera")
async def camera_upload(photo: UploadFile = File(...), name: str = Form(""),
                        task_id: str = Form(...)) -> Dict[str, Any]:
    if show.opts.offline:
        raise HTTPException(503, "The show is running --offline, so photos can't be graded.")
    task = next((t for t in show.tasks if t.task_id == task_id), None)
    if task is None:
        raise HTTPException(404, "unknown question")
    data = await photo.read()
    if not data:
        raise HTTPException(400, "empty photo")
    if len(data) > 25_000_000:
        raise HTTPException(413, "photo too large")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    sub_id = uuid.uuid4().hex[:10]
    suffix = Path(photo.filename or "").suffix.lower() or ".jpg"
    path = UPLOADS / f"{task.task_id}_{sub_id}{suffix}"
    path.write_bytes(data)
    sub = {"id": sub_id, "name": (name.strip() or "Audience")[:40], "status": "reading",
           "received": time.time()}
    with show.lock:
        show.camera.setdefault(task.task_id, []).append(sub)

    def grade() -> None:
        try:
            text = hc.read_handwriting(task, path)
            with show.lock:
                sub.update(transcription=text, status="grading")
            verdict = hc.judge(task, text, model=show.opts.judge_model)
            with show.lock:
                sub.update(judgment=verdict, status="done")
        except Exception as exc:  # noqa: BLE001
            with show.lock:
                sub.update(status="error", error=f"{type(exc).__name__}: {exc}")

    show.pool.submit(grade)
    return {"id": sub_id, "task_id": task.task_id}


@app.get("/api/camera/{task_id}/{sub_id}")
def camera_result(task_id: str, sub_id: str) -> Dict[str, Any]:
    with show.lock:
        idx = next((i for i, t in enumerate(show.tasks) if t.task_id == task_id), None)
        revealed = idx is not None and show.q.get(idx, {}).get("ref", False)
        for sub in show.camera.get(task_id, []):
            if sub["id"] == sub_id:
                # The verdict would spoil the rubric, so it waits for the host's reveal.
                out = {k: v for k, v in sub.items() if revealed or k != "judgment"}
                return {**out, "revealed": revealed}
    raise HTTPException(404, "no such submission")


@app.get("/api/qr.png")
def qr() -> Response:
    import qrcode
    img = qrcode.make(show.opts.camera_url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    global show
    ap = argparse.ArgumentParser(description="HSS quiz show server")
    ap.add_argument("--offline", action="store_true", help="never call Claude; show cached answers")
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 lets phones on the same Wi-Fi connect")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default=hc.ANSWER_MODEL)
    ap.add_argument("--effort", default=hc.ANSWER_EFFORT, choices=["low", "medium", "high", "xhigh", "max"],
                    help="effort for live calls (lower it for quicker live answers)")
    ap.add_argument("--judge-model", default=hc.JUDGE_MODEL)
    ap.add_argument("--live-grace", type=float, default=45.0,
                    help="after C, seconds to wait for a still-running live call before showing the cache")
    ap.add_argument("--no-live-judge", action="store_true", help="don't grade live answers")
    opts = ap.parse_args()

    saved = hc.load_json(SHORTLIST, None)
    if not saved:
        raise SystemExit(f"No shortlist at {SHORTLIST}. Run `python prepare.py` first.")
    tasks = [hc.Task.from_dict(d) for d in saved["tasks"]]
    missing = [t.task_id for t in tasks if not t.media_file.exists()]
    if missing:
        raise SystemExit(f"Media missing for {len(missing)} tasks; re-run prepare.py.")
    cache = hc.load_json(CACHE, {})
    if not opts.offline:
        import os
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("note: ANTHROPIC_API_KEY is not set; live calls will fail and fall back to the cache.")
    opts.camera_url = f"http://{lan_ip()}:{opts.port}/camera"
    show = Show(tasks, cache, opts)

    # Only the downloaded media are served; the cache and uploads stay private.
    (hc.DATA_DIR / "hss").mkdir(parents=True, exist_ok=True)
    app.mount("/media/hss", StaticFiles(directory=hc.DATA_DIR / "hss"), name="media")
    print(f"\n  Projector:  http://localhost:{opts.port}/")
    print(f"  Phones:     {opts.camera_url}")
    print(f"  {len(tasks)} questions, {sum(t.task_id in cache for t in tasks)} with cached answers"
          f"{', OFFLINE' if opts.offline else ''}\n")
    uvicorn.run(app, host=opts.host, port=opts.port, log_level="warning")


if __name__ == "__main__":
    main()
