"""Shared pieces for the HSS quiz demo: dataset rows, media prep, Claude calls.

The answering and grading prompts follow the HSS paper's evaluation protocol
(arXiv 2610.08966, Appendix C.6), as published in the authors' harness
(github.com/xingang2/HSS-Eval, `hss_eval/prompts.py`):

* the answering model gets no system prompt, just one user turn holding a
  one-line media preamble, the media, and "Question: <prompt>";
* videos go in as frames sampled uniformly at 2 fps (capped at 500), scaled to
  a longest side of 768 px, each preceded by a "[frame t=..s]" label;
* a text-only judge (Claude Opus 5 at high effort in the paper) marks every
  rubric criterion met / not met; an answer passes only if all are met.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HSS_DATA_DIR") or ROOT / "data")

ANSWER_MODEL = "claude-opus-5-5"
ANSWER_EFFORT = "xhigh"          # what the paper's registry uses for Claude Opus 5.5
JUDGE_MODEL = "claude-opus-5"    # the paper's headline judge
JUDGE_EFFORT = "high"
READER_MODEL = "claude-opus-5-5"  # reads handwritten answers on the /camera page

VIDEO_FPS = 2.0
VIDEO_MAX_FRAMES = 500
FRAME_MAX_SIDE = 768
IMAGE_MAX_SIDE = 4096
IMAGE_MAX_BYTES = 4_500_000

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# --------------------------------------------------------------------------
# Prompts (verbatim from the paper's harness)
# --------------------------------------------------------------------------
VIDEO_FRAMES_PREAMBLE = (
    "The video below is provided as {num_frames} frames sampled uniformly at {fps:.4g} frames "
    "per second (video duration {duration:.2f}s), in chronological order. "
    "Frame timestamps in seconds: {timestamps}.\n"
    "Treat the frames as one continuous video and reason about motion and event order across "
    "them."
)

IMAGE_PREAMBLE = "An image is attached."

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial grader for the Humanity's Sixth Sense (HSS) visual reasoning "
    "benchmark. You are given a question about a piece of media, a human-written reference "
    "answer that is authoritative and correct, a list of rubric criteria, and a candidate "
    "answer from a model under evaluation.\n\n"
    "Grade the candidate answer against each rubric criterion independently.\n\n"
    "Rules:\n"
    "1. A criterion is met only if the candidate answer actually asserts what the criterion "
    "requires. Do not infer or give credit for near-misses, hedges, or contradictions.\n"
    "2. Wording may differ from the reference; judge meaning, not phrasing. Synonyms, "
    "equivalent directions (e.g. 'right to left' vs 'leftward'), and equivalent units count.\n"
    "3. If the candidate answer states the required content but also states a contradicting "
    "alternative, the criterion is NOT met.\n"
    "4. Extra correct detail neither helps nor hurts. Extra incorrect detail only matters if it "
    "contradicts a criterion.\n"
    "5. Treat the reference answer as ground truth when the candidate disagrees with it.\n"
    "6. Judge only the criteria given. Do not invent criteria.\n\n"
    "Return ONLY a JSON object, no prose and no markdown fences, in exactly this form:\n"
    '{"criteria": [{"id": "<criterion id>", "met": true|false, '
    '"justification": "<one short sentence citing the candidate answer>"}], '
    '"overall_comment": "<one sentence overall assessment>"}'
)

JUDGE_USER_TEMPLATE = """\
## Question
{prompt}

## Media
type: {media_kind}

## Reference answer (ground truth)
{golden_response}

## Rubric criteria
{criteria_block}

## Candidate answer (from the model under evaluation)
<candidate_answer>
{answer}
</candidate_answer>

Grade each criterion. Output the JSON object described in the system prompt, with exactly \
{num_criteria} entries in "criteria", one per criterion id, in the same order.\
"""

# Not from the paper: the /camera page's handwriting reader.
HANDWRITING_PROMPT = (
    "This photo shows a quiz contestant's handwritten answer to the question below. "
    "Transcribe exactly what is written, word for word, keeping the contestant's wording "
    "and spelling. Do not answer the question yourself, correct the answer, or add anything. "
    "If part of it is illegible, write [illegible] in its place. If the photo shows no "
    "handwritten answer, reply with exactly: [no answer found]\n\n"
    "Reply with the transcription only.\n\n"
    "Question the contestant was answering: {prompt}"
)


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
@dataclass
class Task:
    task_id: str
    prompt: str
    media_path: str          # relative to the dataset root, as in data/test.jsonl
    media_kind: str          # "image" | "video"
    domain: str
    subdomain: str
    golden_response: str
    criteria: List[Dict[str, str]]
    credit: str = ""
    duration: Optional[float] = None
    local_media: str = ""    # relative to DATA_DIR once downloaded
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    @property
    def media_file(self) -> Path:
        return DATA_DIR / self.local_media


def parse_criteria(raw: Any) -> List[Dict[str, str]]:
    """`rubric_criteria` is a list of {"id", "title"} objects, or its JSON string."""
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip() else []
    out = []
    for i, item in enumerate(raw or [], start=1):
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            cid = str(item.get("id") or f"c{i}")
        else:
            title, cid = str(item).strip(), f"c{i}"
        if title:
            out.append({"id": cid, "title": title})
    return out


def task_from_row(row: Dict[str, Any]) -> Task:
    known = {"task_id", "prompt", "media_path", "media_type", "domain", "subdomain",
             "golden_response", "rubric_criteria"}
    return Task(
        task_id=str(row["task_id"]),
        prompt=str(row["prompt"]),
        media_path=str(row["media_path"]),
        media_kind=str(row["media_type"]).strip().lower(),
        domain=str(row.get("domain") or ""),
        subdomain=str(row.get("subdomain") or ""),
        golden_response=str(row.get("golden_response") or ""),
        criteria=parse_criteria(row.get("rubric_criteria")),
        duration=row_duration(row),
        extra={k: v for k, v in row.items() if k not in known},
    )


def row_duration(row: Dict[str, Any]) -> Optional[float]:
    for key in ("duration", "duration_sec", "duration_s", "video_duration", "length_sec"):
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


# Columns that might carry media attribution. The public loader only reads the
# columns above, so which (if any) of these exist is discovered at prepare time.
CREDIT_KEYS = ("credit", "credits", "media_credit", "attribution", "media_attribution",
               "author", "creator", "uploader", "photographer", "copyright", "owner")
LICENSE_KEYS = ("license", "licence", "media_license", "license_name")
SOURCE_KEYS = ("source", "source_url", "media_source", "media_url", "original_url", "url")


def credit_from_fields(fields: Dict[str, Any]) -> str:
    lower = {str(k).lower(): v for k, v in fields.items() if v not in (None, "", [], {})}
    parts = []
    for keys, label in ((CREDIT_KEYS, ""), (LICENSE_KEYS, "License: "), (SOURCE_KEYS, "Source: ")):
        for key in keys:
            if key in lower:
                parts.append(f"{label}{lower[key]}")
                break
    return " · ".join(str(p) for p in parts)


def default_credit(task: Task) -> str:
    return (f"Media: Humanity's Sixth Sense benchmark (Scale AI & Elorian), "
            f"dataset ScaleAI/HSS, task {task.task_id} ({task.media_path}). "
            f"See the dataset card's media notice.")


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------
def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"`{name}` not found on PATH. On macOS: brew install ffmpeg")
    return path


def probe_duration(source: str, headers: Optional[str] = None, timeout: float = 60) -> float:
    """Duration in seconds of a local file or URL (ffprobe reads only what it needs)."""
    cmd = [require_tool("ffprobe"), "-v", "error"]
    if headers:
        cmd += ["-headers", headers]
    cmd += ["-show_entries", "format=duration:stream=duration", "-of", "json", source]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300] or "ffprobe failed")
    doc = json.loads(proc.stdout or "{}")
    candidates = [doc.get("format", {}).get("duration")]
    candidates += [s.get("duration") for s in doc.get("streams") or []]
    for value in candidates:
        try:
            d = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(d) and d > 0:
            return d
    raise RuntimeError("no duration reported")


def sample_frames(video: Path, out_dir: Path) -> tuple[List[Path], List[float], float, float]:
    """Uniform frames at 2 fps (fewer if that exceeds 500), longest side 768 px.

    Returns (paths, timestamps, effective_fps, duration). Frames are cached in
    `out_dir`; a marker file records the settings they were made with.
    """
    duration = probe_duration(str(video))
    target = max(1, int(math.floor(duration * VIDEO_FPS)))
    fps = VIDEO_FPS if target <= VIDEO_MAX_FRAMES else VIDEO_MAX_FRAMES / duration
    marker = out_dir / "_sampling.json"
    settings = {"fps": fps, "max_side": FRAME_MAX_SIDE, "src_size": video.stat().st_size}
    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not (marker.exists() and frames and json.loads(marker.read_text()) == settings):
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in frames:
            f.unlink()
        scale = (f"scale=w='min({FRAME_MAX_SIDE},iw)':h='min({FRAME_MAX_SIDE},ih)'"
                 ":force_original_aspect_ratio=decrease:flags=lanczos")
        def run(passthrough: List[str]) -> subprocess.CompletedProcess:
            cmd = [require_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(video),
                   "-vf", f"fps={fps:.10g},{scale}", *passthrough,
                   "-frames:v", str(VIDEO_MAX_FRAMES), "-q:v", "3",
                   str(out_dir / "frame_%05d.jpg")]
            return subprocess.run(cmd, capture_output=True, text=True, check=False)

        # ffmpeg 5.1+ spells it -fps_mode; ffmpeg 8 removed the older -vsync.
        proc = run(["-fps_mode", "passthrough"])
        if proc.returncode != 0 and "fps_mode" in proc.stderr:
            proc = run(["-vsync", "0"])
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on {video.name}: {proc.stderr.strip()[:300]}")
        frames = sorted(out_dir.glob("frame_*.jpg"))
        if not frames:
            raise RuntimeError(f"ffmpeg produced no frames for {video.name}")
        marker.write_text(json.dumps(settings))
    timestamps = [(int(p.stem.split("_")[-1]) - 1) / fps for p in frames]
    return frames, timestamps, fps, duration


def format_timestamps(ts: List[float], max_listed: int = 24) -> str:
    if not ts:
        return "(none)"
    if len(ts) <= max_listed:
        return ", ".join(f"{t:.2f}" for t in ts)
    head = ", ".join(f"{t:.2f}" for t in ts[:8])
    tail = ", ".join(f"{t:.2f}" for t in ts[-4:])
    return f"{head}, ... (evenly spaced), {tail}"


_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}


def image_block(path: Path, max_side: int = IMAGE_MAX_SIDE, max_bytes: int = IMAGE_MAX_BYTES,
                exif_rotate: bool = False) -> Dict[str, Any]:
    """Base64 image block at native resolution, downscaled only if it is too big."""
    raw = path.read_bytes()
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    needs_work = exif_rotate or media_type is None or len(raw) > max_bytes
    if not needs_work:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            needs_work = max(im.size) > max_side
    if needs_work:
        raw, media_type = _reencode(raw, max_side, max_bytes, exif_rotate)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(raw).decode()}}


def _reencode(raw: bytes, max_side: int, max_bytes: int, exif_rotate: bool) -> tuple[bytes, str]:
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(raw))
    if exif_rotate:
        im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    side = min(max_side, max(im.size))
    while True:
        scaled = im.copy()
        scaled.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        scaled.save(buf, "JPEG", quality=90)
        if buf.tell() <= max_bytes or side <= 512:
            return buf.getvalue(), "image/jpeg"
        side = int(side * 0.8)


def build_answer_content(task: Task, frames_root: Path) -> List[Dict[str, Any]]:
    """One user turn: media preamble, media, then the question verbatim."""
    if task.media_kind == "video":
        frames, ts, fps, duration = sample_frames(task.media_file, frames_root / task.task_id)
        content: List[Dict[str, Any]] = [{"type": "text", "text": VIDEO_FRAMES_PREAMBLE.format(
            num_frames=len(frames), fps=fps, duration=duration, timestamps=format_timestamps(ts))}]
        for path, t in zip(frames, ts):
            content.append({"type": "text", "text": f"[frame t={t:.2f}s]"})
            content.append(image_block(path))
    else:
        content = [{"type": "text", "text": IMAGE_PREAMBLE}, image_block(task.media_file)]
    content.append({"type": "text", "text": f"Question: {task.prompt}"})
    return content


# --------------------------------------------------------------------------
# Claude calls
# --------------------------------------------------------------------------
_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=4, timeout=1800)
    return _client


def _call(model: str, effort: str, messages: List[Dict[str, Any]], max_tokens: int,
          system: Optional[str] = None, summarize_thinking: bool = False) -> Dict[str, Any]:
    """Stream one request and return its text, thinking summary and metadata."""
    kwargs: Dict[str, Any] = dict(
        model=model, max_tokens=max_tokens, messages=messages,
        output_config={"effort": effort},
        thinking={"type": "adaptive", "display": "summarized" if summarize_thinking else "omitted"},
        betas=[FALLBACK_BETA], fallbacks="default",
    )
    if system:
        kwargs["system"] = system
    started = time.time()
    with client().beta.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    thinking = "\n\n".join(b.thinking for b in msg.content
                           if b.type == "thinking" and getattr(b, "thinking", "")).strip()
    if msg.stop_reason == "refusal":
        category = getattr(getattr(msg, "stop_details", None), "category", None)
        raise RuntimeError(f"Claude declined this request (refusal: {category or 'unspecified'})")
    if not text:
        raise RuntimeError(f"empty response (stop_reason={msg.stop_reason})")
    usage = msg.usage
    return {
        "text": text, "thinking": thinking, "model": msg.model, "effort": effort,
        "stop_reason": msg.stop_reason, "latency_s": round(time.time() - started, 1),
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def ask_claude(task: Task, frames_root: Path, model: str = ANSWER_MODEL,
               effort: str = ANSWER_EFFORT) -> Dict[str, Any]:
    content = build_answer_content(task, frames_root)
    out = _call(model, effort, [{"role": "user", "content": content}],
                max_tokens=128000, summarize_thinking=True)
    out["answer"] = out.pop("text")
    return out


def judge(task: Task, answer: str, model: str = JUDGE_MODEL,
          effort: str = JUDGE_EFFORT) -> Dict[str, Any]:
    """Grade `answer` against the task's rubric with the paper's judge prompt."""
    criteria_block = "\n".join(f"{i}. [id: {c['id']}] {c['title']}"
                               for i, c in enumerate(task.criteria, 1)) or "(no criteria provided)"
    user = JUDGE_USER_TEMPLATE.format(
        prompt=task.prompt, media_kind=task.media_kind, golden_response=task.golden_response,
        criteria_block=criteria_block,
        answer=answer.strip() or "(the model returned an empty answer)",
        num_criteria=len(task.criteria))
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user}]
    last_err = None
    for _ in range(2):
        out = _call(model, effort, messages, max_tokens=8192, system=JUDGE_SYSTEM_PROMPT)
        try:
            return _score(task, extract_json_object(out["text"]), out)
        except ValueError as exc:
            last_err = exc
            messages = messages + [
                {"role": "assistant", "content": out["text"]},
                {"role": "user", "content": "Your reply was not valid. Return ONLY the JSON object, "
                 "no prose, with one entry per criterion id in "
                 f"{json.dumps([c['id'] for c in task.criteria])}."}]
    raise RuntimeError(f"judge output unparseable: {last_err}")


def _score(task: Task, parsed: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    verdicts = {str(c.get("id")): c for c in parsed.get("criteria") or [] if isinstance(c, dict)}
    rows = []
    for i, crit in enumerate(task.criteria):
        v = verdicts.get(crit["id"])
        if v is None and len(verdicts) == len(task.criteria):
            v = list(verdicts.values())[i]
        if v is None:
            raise ValueError(f"missing verdict for criterion {crit['id']}")
        rows.append({"id": crit["id"], "title": crit["title"], "met": v.get("met") is True,
                     "justification": str(v.get("justification") or "")})
    return {"criteria": rows, "passed": bool(rows) and all(r["met"] for r in rows),
            "overall_comment": str(parsed.get("overall_comment") or ""),
            "model": meta["model"], "latency_s": meta["latency_s"]}


def extract_json_object(text: str) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found")
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    return obj


def read_handwriting(task: Task, photo: Path, model: str = READER_MODEL) -> str:
    content = [image_block(photo, max_side=2048, exif_rotate=True),
               {"type": "text", "text": HANDWRITING_PROMPT.format(prompt=task.prompt)}]
    return _call(model, "medium", [{"role": "user", "content": content}], max_tokens=4096)["text"]


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
