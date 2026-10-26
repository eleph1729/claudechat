"""Build the quiz: shortlist HSS tasks, fetch their media, cache Claude's answers.

    python prepare.py                     # ~20 images + ~15 videos, answered and judged
    python prepare.py --skip-claude       # shortlist + media only
    python prepare.py --reselect          # pick a new shortlist (new --seed for a different one)

Steps (each resumable; re-running only does what is missing):
 1. download data/test.jsonl from the ScaleAI/HSS dataset (no media yet);
 2. pick a domain-balanced shortlist: images, plus videos under --max-video-seconds
    (durations are read with ffprobe straight from the Hub, without downloading);
 3. download only the shortlisted media;
 4. ask Claude each question (paper protocol) and grade the answer with the
    paper's rubric judge; results go to data/cache/answers.json.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import hss_common as hc

SHORTLIST = hc.DATA_DIR / "shortlist.json"
CACHE = hc.DATA_DIR / "cache" / "answers.json"
FRAMES = hc.DATA_DIR / "frames"
MEDIA_ROOT = "hss"   # media land in data/hss/<media_path>
CREDIT_OVERRIDES = hc.ROOT / "credits.json"


# --------------------------------------------------------------------------
# Dataset access: the Hub, or a local copy laid out like it
# --------------------------------------------------------------------------
class Source:
    def __init__(self, spec: str):
        self.spec = spec
        self.local = Path(spec).expanduser() if Path(spec).expanduser().is_dir() else None

    def label(self) -> str:
        return str(self.local) if self.local else f"huggingface.co/datasets/{self.spec}"

    def rows(self) -> List[Dict[str, Any]]:
        path = self.fetch("data/test.jsonl", into_cache=True)
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def files(self) -> List[str]:
        if self.local:
            return [str(p.relative_to(self.local)) for p in self.local.rglob("*") if p.is_file()]
        from huggingface_hub import list_repo_files
        return list_repo_files(self.spec, repo_type="dataset")

    def fetch(self, rel: str, into_cache: bool = False) -> Path:
        """Local path of `rel`; Hub files are downloaded (and cached) first."""
        if self.local:
            return self.local / rel
        from huggingface_hub import hf_hub_download
        if into_cache:
            return Path(hf_hub_download(self.spec, rel, repo_type="dataset"))
        return Path(hf_hub_download(self.spec, rel, repo_type="dataset",
                                    local_dir=hc.DATA_DIR / MEDIA_ROOT))

    def probe_target(self, rel: str) -> tuple[str, Optional[str]]:
        """(URL or path, ffprobe -headers value) for reading a file's duration in place."""
        if self.local:
            return str(self.local / rel), None
        from huggingface_hub import get_token, hf_hub_url
        token = get_token()
        url = hf_hub_url(self.spec, rel, repo_type="dataset")
        hub_host = urllib.parse.urlsplit(url).netloc
        # Follow the Hub's redirects ourselves: the final CDN URL is pre-signed and
        # may reject a request that also carries an Authorization header.
        for _ in range(5):
            if urllib.parse.urlsplit(url).netloc != hub_host:
                return url, None
            req = urllib.request.Request(url, method="HEAD")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            try:
                urllib.request.build_opener(_NoRedirect).open(req, timeout=30).close()
                break                      # served directly by the Hub
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308) or not exc.headers.get("Location"):
                    raise
                url = urllib.parse.urljoin(url, exc.headers["Location"])
        same_host = urllib.parse.urlsplit(url).netloc == hub_host
        return url, (f"Authorization: Bearer {token}\r\n" if token and same_host else None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def explain_hub_error(exc: Exception) -> str:
    name = type(exc).__name__
    if "Gated" in name or "401" in str(exc) or "403" in str(exc):
        return ("The dataset refused access. Open https://huggingface.co/datasets/ScaleAI/HSS, "
                "accept its terms, then run `hf auth login` (or export HF_TOKEN) and retry.")
    return f"{name}: {exc}"


# --------------------------------------------------------------------------
# Credits
# --------------------------------------------------------------------------
def credit_index(src: Source, files: List[str]) -> Dict[str, str]:
    """Credit strings keyed by task_id / media_path / basename, from any credits-like
    file the dataset ships (CSV/TSV/JSON/JSONL), plus the local credits.json."""
    index: Dict[str, str] = {}
    hits = [f for f in files
            if any(w in Path(f).name.lower() for w in ("credit", "attribution", "licen", "source"))
            and Path(f).suffix.lower() in {".csv", ".tsv", ".json", ".jsonl"}
            and not f.startswith("media/")]
    for rel in hits:
        try:
            rows = _read_table(src.fetch(rel, into_cache=True))
        except Exception as exc:  # noqa: BLE001 - a bad side file must not stop the run
            print(f"  (could not read {rel}: {exc})")
            continue
        print(f"  credits file: {rel} ({len(rows)} rows)")
        for row in rows:
            text = hc.credit_from_fields(row)
            if not text:
                continue
            for key in ("task_id", "media_path", "file", "filename", "path"):
                if row.get(key):
                    index[str(row[key])] = text
                    index[Path(str(row[key])).name] = text
    for key, text in hc.load_json(CREDIT_OVERRIDES, {}).items():
        index[str(key)] = str(text)
    return index


def _read_table(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
        if isinstance(doc, dict):
            return [{"task_id": k, **(v if isinstance(v, dict) else {"credit": v})}
                    for k, v in doc.items()]
        return [r for r in doc if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(text), delimiter="\t" if path.suffix == ".tsv" else ","))


def assign_credit(task: hc.Task, index: Dict[str, str]) -> None:
    task.credit = (index.get(task.task_id) or index.get(task.media_path)
                   or index.get(Path(task.media_path).name)
                   or hc.credit_from_fields(task.extra) or hc.default_credit(task))


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------
def balanced_order(tasks: List[hc.Task], seed: int) -> List[hc.Task]:
    """Round-robin over domains, and over subdomains within each domain."""
    rng = random.Random(seed)
    by_domain: Dict[str, Dict[str, List[hc.Task]]] = defaultdict(lambda: defaultdict(list))
    for t in tasks:
        by_domain[t.domain][t.subdomain].append(t)
    streams = {}
    for domain, subs in by_domain.items():
        lists = [rng.sample(v, len(v)) for _, v in sorted(subs.items())]
        rng.shuffle(lists)
        streams[domain] = _round_robin(lists)
    domains = sorted(streams)
    rng.shuffle(domains)
    return _round_robin([streams[d] for d in domains])


def _round_robin(lists: List[List[hc.Task]]) -> List[hc.Task]:
    out, i = [], 0
    while any(lists):
        lst = lists[i % len(lists)]
        if lst:
            out.append(lst.pop(0))
        i += 1
    return out


def pick(candidates: List[hc.Task], n: int, accept: Callable[[hc.Task], bool]) -> List[hc.Task]:
    """First `n` acceptable candidates in balanced order, at most n/#domains per domain
    on the first pass; a second pass fills any shortfall from the larger domains."""
    domains = {t.domain for t in candidates} or {""}
    quota = -(-n // len(domains))
    chosen: List[hc.Task] = []
    counts: Counter = Counter()
    rejected: set = set()
    picked: set = set()
    for relaxed in (False, True):
        for t in candidates:
            if len(chosen) >= n:
                return chosen
            if t.task_id in picked or t.task_id in rejected or (not relaxed and counts[t.domain] >= quota):
                continue
            if accept(t):
                chosen.append(t)
                picked.add(t.task_id)
                counts[t.domain] += 1
            else:
                rejected.add(t.task_id)
    return chosen


def interleave(images: List[hc.Task], videos: List[hc.Task]) -> List[hc.Task]:
    """Spread the videos evenly through the show."""
    out, ii, vi, total = [], 0, 0, len(images) + len(videos)
    for i in range(total):
        if vi < round((i + 1) * len(videos) / total) or ii >= len(images):
            out.append(videos[vi])
            vi += 1
        else:
            out.append(images[ii])
            ii += 1
    return out


def select(src: Source, args) -> List[hc.Task]:
    print(f"Reading task list from {src.label()} ...")
    rows = src.rows()
    print(f"  {len(rows)} tasks; columns: {', '.join(sorted({k for r in rows for k in r}))}")
    tasks = [hc.task_from_row(r) for r in rows]
    tasks = [t for t in tasks if t.criteria and t.golden_response]
    files = src.files()
    credits = credit_index(src, files)
    for t in tasks:
        assign_credit(t, credits)

    images = balanced_order([t for t in tasks if t.media_kind == "image"], args.seed)
    videos = balanced_order([t for t in tasks if t.media_kind == "video"], args.seed)

    chosen_images = pick(images, args.images, lambda t: True)

    def short_enough(t: hc.Task) -> bool:
        if t.duration is None:
            try:
                target, headers = src.probe_target(t.media_path)
                t.duration = hc.probe_duration(target, headers)
            except Exception as exc:  # noqa: BLE001
                print(f"  probe failed for {t.task_id} ({exc}); downloading it to check")
                try:
                    t.duration = hc.probe_duration(str(src.fetch(t.media_path)))
                except Exception as exc2:  # noqa: BLE001
                    print(f"  skipping {t.task_id}: {exc2}")
                    return False
        ok = t.duration < args.max_video_seconds
        print(f"  video {t.task_id}: {t.duration:.1f}s {'ok' if ok else '(too long)'}")
        return ok

    print(f"Probing video durations (want {args.videos} under {args.max_video_seconds:g}s) ...")
    chosen_videos = pick(videos, args.videos, short_enough)
    show = interleave(chosen_images, chosen_videos)
    print(f"Shortlist: {len(chosen_images)} images, {len(chosen_videos)} videos")
    for domain, n in sorted(Counter(t.domain for t in show).items()):
        print(f"  {n:3d}  {domain}")
    return show


def download(src: Source, tasks: Iterable[hc.Task]) -> None:
    print("Downloading shortlisted media ...")
    for t in tasks:
        dest = hc.DATA_DIR / MEDIA_ROOT / t.media_path
        if not dest.exists():
            got = src.fetch(t.media_path)
            if src.local or got.resolve() != dest.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(got, dest)
        t.local_media = f"{MEDIA_ROOT}/{t.media_path}"
        if t.media_kind == "video" and t.duration is None:
            t.duration = hc.probe_duration(str(dest))
    print(f"  media in {hc.DATA_DIR / MEDIA_ROOT}")


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------
def answer_all(tasks: List[hc.Task], args) -> None:
    cache = hc.load_json(CACHE, {})
    lock = threading.Lock()
    todo = [t for t in tasks if args.force or "answer" not in cache.get(t.task_id, {})
            or (not args.no_judge and "judgment" not in cache.get(t.task_id, {}))]
    if not todo:
        print("All answers already cached.")
        return
    print(f"Asking {args.model} (effort {args.effort}) {len(todo)} questions, "
          f"{args.workers} at a time ...")

    def work(t: hc.Task) -> str:
        entry = dict(cache.get(t.task_id, {}))
        if args.force or "answer" not in entry:
            entry = hc.ask_claude(t, FRAMES, model=args.model, effort=args.effort)
            entry.pop("judgment", None)
        if not args.no_judge and "judgment" not in entry:
            entry["judgment"] = hc.judge(t, entry["answer"], model=args.judge_model)
        with lock:
            cache[t.task_id] = entry
            hc.save_json(CACHE, cache)
        verdict = entry.get("judgment", {}).get("passed")
        return (f"{t.task_id} [{t.media_kind}] {entry['latency_s']}s "
                f"{'PASS' if verdict else 'FAIL' if verdict is False else ''}")

    failures = 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(work, t): t for t in todo}
        for fut in as_completed(futures):
            try:
                print("  " + fut.result())
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  {futures[fut].task_id}: FAILED: {exc}")
    passed = [e.get("judgment", {}).get("passed") for e in cache.values()]
    if any(p is not None for p in passed):
        print(f"Claude passed {sum(bool(p) for p in passed)}/{sum(p is not None for p in passed)} "
              "cached questions.")
    if failures:
        print(f"{failures} failed; re-run prepare.py to retry just those.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="ScaleAI/HSS", help="Hub id, or a local copy of the dataset repo")
    ap.add_argument("--images", type=int, default=20)
    ap.add_argument("--videos", type=int, default=15)
    ap.add_argument("--max-video-seconds", type=float, default=45.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reselect", action="store_true", help="discard the saved shortlist and pick again")
    ap.add_argument("--skip-claude", action="store_true", help="stop after downloading media")
    ap.add_argument("--no-judge", action="store_true", help="cache answers without grading them")
    ap.add_argument("--force", action="store_true", help="re-ask Claude even if an answer is cached")
    ap.add_argument("--model", default=hc.ANSWER_MODEL)
    ap.add_argument("--effort", default=hc.ANSWER_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--judge-model", default=hc.JUDGE_MODEL)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    hc.require_tool("ffmpeg"), hc.require_tool("ffprobe")
    src = Source(args.dataset)
    try:
        saved = hc.load_json(SHORTLIST, None)
        if saved and not args.reselect:
            tasks = [hc.Task.from_dict(d) for d in saved["tasks"]]
            print(f"Using saved shortlist ({len(tasks)} tasks; --reselect to pick again)")
        else:
            tasks = select(src, args)
        download(src, tasks)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__module__.startswith("huggingface_hub"):
            print("ERROR: " + explain_hub_error(exc), file=sys.stderr)
            return 1
        raise
    hc.save_json(SHORTLIST, {"dataset": args.dataset, "seed": args.seed,
                             "tasks": [t.to_dict() for t in tasks]})
    print(f"Saved {SHORTLIST}")

    if not args.skip_claude:
        answer_all(tasks, args)
    print("Done. Start the show with:  python server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
