"""HSS quiz show: random questions from the whole dataset, Claude answering live.

    python server.py                  # then open http://localhost:8000

Each question's media is downloaded when it's needed (into the Hugging Face
cache), and the next random question downloads in the background while the
current one is on screen. Claude starts answering the moment a question
appears. Videos show their length up front and start playing after a short
countdown, so the audience can read the question first.
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import hss_common as hc

STATIC = hc.ROOT / "static"
FRAMES = hc.DATA_DIR / "frames"


class Show:
    def __init__(self, ds: hc.Dataset, tasks: List[hc.Task], opts: argparse.Namespace):
        self.ds = ds
        self.tasks = tasks
        self.opts = opts
        self.rng = random.Random(opts.seed)
        self.order: List[hc.Task] = []
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.history: List[Dict[str, Any]] = []   # questions drawn so far, in order
        self.index = -1                           # -1 = title screen

    # ---- drawing and loading questions -------------------------------------
    def draw(self) -> hc.Task:
        """Next random task; every task comes up once before any repeats."""
        if not self.order:
            self.order = self.rng.sample(self.tasks, len(self.tasks))
        return self.order.pop()

    def add_entry(self) -> Dict[str, Any]:
        entry = {"task": self.draw(), "status": "loading", "error": None, "shown_at": None,
                 "play_at": None, "pens_down_at": None, "claude": False, "ref": False,
                 "human": False, "ai": False, "skipped": False, "job": None}
        self.history.append(entry)
        self.pool.submit(self.load, entry)
        return entry

    def load(self, entry: Dict[str, Any]) -> None:
        task = entry["task"]
        try:
            path = self.ds.fetch(task.media_path)
            duration = hc.probe_duration(str(path)) if task.media_kind == "video" else None
            with self.lock:
                task.local_path = str(path)
                task.duration = duration or task.duration
                entry["status"] = "ready"
                if self.current() is entry:
                    self.activate(entry)
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
            print(f"[load] {task.task_id} failed: {exc}")

    def current(self) -> Optional[Dict[str, Any]]:
        return self.history[self.index] if 0 <= self.index < len(self.history) else None

    def activate(self, entry: Dict[str, Any]) -> None:
        """The question is on screen: start the clock, the countdown and Claude."""
        if entry["shown_at"] is not None or entry["status"] != "ready":
            return
        now = time.time()
        entry["shown_at"] = now
        if entry["task"].media_kind == "video":
            entry["play_at"] = now + self.opts.countdown
        self.start_claude(entry)

    def go(self, i: int) -> None:
        self.index = max(-1, i)
        while self.index >= len(self.history):
            self.add_entry()
        entry = self.current()
        if entry:
            self.activate(entry)
            if self.index + 1 >= len(self.history):
                self.add_entry()                  # prefetch the next question

    # ---- Claude ---------------------------------------------------------
    def start_claude(self, entry: Dict[str, Any]) -> None:
        task = entry["task"]
        job: Dict[str, Any] = {"status": "pending", "started": time.time()}
        entry["job"] = job

        def run() -> None:
            try:
                out = hc.ask_claude(task, FRAMES, model=self.opts.model, effort=self.opts.effort)
                with self.lock:
                    job.update(out, status="done")
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
                print(f"[claude] {task.task_id} failed: {exc}")

        self.pool.submit(run)

    # ---- host actions -------------------------------------------------------
    def action(self, name: str) -> None:
        with self.lock:
            entry = self.current()
            if name == "next":
                self.go(self.index + 1)
            elif name == "back":
                self.go(self.index - 1)
            elif name == "restart":
                self.history.clear()
                self.index = -1
            elif not entry:
                return
            elif name == "skip":
                entry.update(skipped=True, human=False, ai=False)
                self.go(self.index + 1)
            elif name == "pens":
                entry["pens_down_at"] = None if entry["pens_down_at"] else time.time()
            elif name == "play" and entry["play_at"]:
                entry["play_at"] = min(entry["play_at"], time.time())
            elif name == "claude":
                job = entry["job"]
                if entry["claude"] and job and job["status"] == "error":
                    self.start_claude(entry)      # C again after an error retries
                entry["claude"] = True
                entry["pens_down_at"] = entry["pens_down_at"] or time.time()
            elif name == "ref":
                entry["ref"] = not entry["ref"]
            elif name in ("human", "ai"):
                entry[name] = not entry[name]
                entry["skipped"] = False

    # ---- state for the page ---------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            shown = [e for e in self.history if e["shown_at"]]
            out: Dict[str, Any] = {
                "index": self.index, "dataset_size": len(self.tasks), "now": time.time(),
                "scores": {"human": sum(e["human"] for e in self.history),
                           "ai": sum(e["ai"] for e in self.history),
                           "played": sum(1 for e in shown if not e["skipped"])},
                "model": self.opts.model, "long_video": self.opts.long_video, "question": None}
            entry = self.current()
            if not entry:
                return out
            task = entry["task"]
            q: Dict[str, Any] = {
                "number": self.index + 1, "task_id": task.task_id, "status": entry["status"],
                "error": entry["error"], "prompt": task.prompt, "kind": task.media_kind,
                "domain": task.domain, "subdomain": task.subdomain, "credit": task.credit,
                "duration": task.duration,
                "media_url": f"/media/{self.index}/{task.task_id}" if entry["status"] == "ready" else None,
                **{k: entry[k] for k in ("shown_at", "play_at", "pens_down_at", "claude", "ref",
                                         "human", "ai", "skipped")}}
            job = entry["job"]
            if job:
                q["claude_status"] = job["status"]
                q["claude_elapsed"] = round(time.time() - job["started"], 1)
            if entry["claude"] and job:
                if job["status"] == "done":
                    q["answer"] = job["answer"]
                    q["thinking"] = job.get("thinking", "")
                    q["answer_meta"] = {k: job.get(k) for k in ("model", "effort", "latency_s")}
                    q["judgment"] = job.get("judgment")
                    q["judge_pending"] = "judgment" not in job and "judge_error" not in job
                elif job["status"] == "error":
                    q["answer_error"] = job["error"]
            if entry["ref"]:
                q["reference"] = task.golden_response
                q["criteria"] = task.criteria
            out["question"] = q
            return out


app = FastAPI()
show: Show  # set in main()


class Action(BaseModel):
    action: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> JSONResponse:
    return JSONResponse(show.snapshot(), headers={"Cache-Control": "no-store"})


@app.post("/api/action")
def act(body: Action) -> JSONResponse:
    allowed = {"next", "back", "skip", "pens", "play", "claude", "ref", "human", "ai", "restart"}
    if body.action not in allowed:
        raise HTTPException(400, f"unknown action {body.action!r}")
    show.action(body.action)
    return state()


@app.get("/media/{i}/{task_id}")
def media(i: int, task_id: str) -> FileResponse:
    with show.lock:
        if not 0 <= i < len(show.history):
            raise HTTPException(404)
        task = show.history[i]["task"]
        if task.task_id != task_id or not task.local_path:
            raise HTTPException(404)
        return FileResponse(task.local_path)


def main() -> None:
    global show
    ap = argparse.ArgumentParser(description="HSS quiz show server")
    ap.add_argument("--dataset", default="ScaleAI/HSS", help="Hub id, or a local copy of the dataset repo")
    ap.add_argument("--kind", choices=["all", "image", "video"], default="all",
                    help="only draw images or only videos")
    ap.add_argument("--countdown", type=float, default=10.0,
                    help="seconds before a video starts playing")
    ap.add_argument("--long-video", type=float, default=60.0,
                    help="videos longer than this (seconds) get a warning-coloured length badge")
    ap.add_argument("--seed", type=int, default=None, help="fix the random order")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default=hc.ANSWER_MODEL)
    ap.add_argument("--effort", default=hc.ANSWER_EFFORT, choices=["low", "medium", "high", "xhigh", "max"],
                    help="lower it for quicker answers")
    ap.add_argument("--judge-model", default=hc.JUDGE_MODEL)
    opts = ap.parse_args()

    import os
    hc.require_tool("ffmpeg"), hc.require_tool("ffprobe")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit("Set ANTHROPIC_API_KEY first: export ANTHROPIC_API_KEY=sk-ant-...")
    ds = hc.Dataset(opts.dataset)
    print(f"Loading the task list from {ds.label()} ...")
    try:
        tasks = ds.load_tasks()
    except Exception as exc:  # noqa: BLE001
        if type(exc).__module__.startswith("huggingface_hub"):
            raise SystemExit("ERROR: " + hc.explain_hub_error(exc))
        raise
    if opts.kind != "all":
        tasks = [t for t in tasks if t.media_kind == opts.kind]
    if not tasks:
        raise SystemExit("No tasks to show.")
    show = Show(ds, tasks, opts)
    with show.lock:
        show.add_entry()                          # start fetching the first question now
    n_img = sum(t.media_kind == "image" for t in tasks)
    print(f"\n  {len(tasks)} questions ({n_img} images, {len(tasks) - n_img} videos)")
    print(f"  Open http://localhost:{opts.port}/ and press F for full screen\n")
    uvicorn.run(app, host=opts.host, port=opts.port, log_level="warning")


if __name__ == "__main__":
    main()
