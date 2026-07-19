"""Orchestration: neutral citation in, citation-status report out.

`run_check` is a generator yielding progress events followed by a final
result event, so the web UI can stream progress to the browser.
"""

from __future__ import annotations

import datetime
import traceback

import anthropic
import requests

from . import classify
from .cache import Cache
from .citation import CitationError, parse_citation
from .fcl import FCLClient, extract_citation_contexts

OVERALL_LABELS = {
    "negative": "Negative treatment found — check before relying on this case",
    "caution": "Treated with caution — read the flagged judgments",
    "positive": "Positive or neutral treatment only within Find Case Law",
    "neutral": "Cited without significant treatment within Find Case Law",
    "unknown": "No citing cases found within Find Case Law",
}

COVERAGE_NOTE = (
    "Coverage is limited to judgments held by Find Case Law (mainly senior courts "
    "of England & Wales from ~2001-2003 onwards, tribunals later). Absence of "
    "negative treatment here is not proof a case is good law: it may be affected "
    "by judgments outside the corpus, by appeals, or by legislation. Treatments "
    "are AI-classified — always read the quoted passages and the judgments."
)


def run_check(citation_text: str, case_name: str | None = None, *,
              fcl: FCLClient | None = None, cache: Cache | None = None,
              limit: int = 25, refresh: bool = False):
    """Yield {"type": "progress"|"result"|"error", ...} events."""
    try:
        target = parse_citation(citation_text)
    except CitationError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    cache = cache if cache is not None else Cache()
    key = target.canonical
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            cached["from_cache"] = True
            yield {"type": "result", "data": cached}
            return

    fcl = fcl or FCLClient()
    client = None
    claude_available = True

    try:
        yield _progress(f"Looking up {target.canonical} on Find Case Law…")
        target_name = case_name
        if target.fcl_uri:
            try:
                judgment = fcl.fetch_judgment(target.fcl_uri)
                target_name = target_name or judgment.name
            except requests.RequestException:
                pass  # target may pre-date the corpus; we can still search

        phrases = [case_name] if case_name else []
        yield _progress("Searching for citing judgments…")
        hits = fcl.find_citing(target, extra_phrases=phrases)
        yield _progress(f"Found {len(hits)} citing judgment(s); analysing up to {limit}…")

        citing = []
        for i, hit in enumerate(hits[:limit], start=1):
            yield _progress(f"[{i}/{min(len(hits), limit)}] {hit.title}")
            try:
                judgment = fcl.fetch_judgment(hit.uri)
            except requests.RequestException as exc:
                citing.append(_entry(hit, judgment=None, classification=classify._result(
                    "unclear", "low", "", f"Could not fetch judgment: {exc}", method="error")))
                continue

            contexts = extract_citation_contexts(judgment.xml, target)
            if not contexts:
                classification = classify._result(
                    "cited", "low", "",
                    "Judgment matched the search but no citation passage was located "
                    "(it may cite by case name or law-report citation only).",
                    method="no-context")
            elif claude_available:
                if client is None:
                    client = classify.make_client()
                try:
                    classification = classify.classify_with_claude(
                        client, target.canonical, target_name, judgment.name, contexts)
                # TypeError is what the SDK raises when no credentials resolve at all.
                except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                        TypeError):
                    claude_available = False
                    yield _progress("Claude API unavailable — falling back to keyword heuristic.")
                    classification = classify.classify_heuristic(contexts)
                except anthropic.APIError:
                    classification = classify.classify_heuristic(contexts)
            else:
                classification = classify.classify_heuristic(contexts)

            citing.append(_entry(hit, judgment, classification, contexts))

        result = _build_result(target, target_name, citing,
                               truncated=len(hits) > limit, total_hits=len(hits),
                               claude_used=claude_available)
        cache.set(key, result)
        yield {"type": "result", "data": result}
    except Exception as exc:  # surface anything unexpected to the UI
        traceback.print_exc()
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


def _progress(message: str) -> dict:
    return {"type": "progress", "message": message}


def _entry(hit, judgment, classification, contexts=None) -> dict:
    return {
        "name": (judgment.name if judgment else None) or hit.title,
        "cite": judgment.cite if judgment else None,
        "date": (judgment.date if judgment else None) or hit.date,
        "uri": hit.uri,
        "link": hit.link,
        "contexts": contexts or [],
        **classification,
    }


def aggregate(citing: list[dict]) -> str:
    if not citing:
        return "unknown"
    max_severity = max(c["severity"] for c in citing)
    if max_severity >= 3:
        return "negative"
    if max_severity == 2:
        return "negative"
    if max_severity == 1:
        return "caution"
    if any(c["polarity"] == "positive" for c in citing):
        return "positive"
    return "neutral"


def _build_result(target, target_name, citing, *, truncated, total_hits, claude_used) -> dict:
    # Most serious treatments first; most recent first within each severity.
    citing = sorted(citing, key=lambda c: c.get("date") or "", reverse=True)
    citing.sort(key=lambda c: c["severity"], reverse=True)
    overall = aggregate(citing)
    counts: dict[str, int] = {}
    for c in citing:
        counts[c["treatment"]] = counts.get(c["treatment"], 0) + 1
    return {
        "citation": target.canonical,
        "uri": target.fcl_uri,
        "case_name": target_name,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "overall": overall,
        "overall_label": OVERALL_LABELS[overall],
        "counts": counts,
        "total_hits": total_hits,
        "truncated": truncated,
        "classifier": "claude" if claude_used else "heuristic",
        "coverage_note": COVERAGE_NOTE,
        "citing": citing,
        "from_cache": False,
    }
