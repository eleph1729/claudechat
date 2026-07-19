"""Treatment classification: how does a citing judgment treat the target case?

Primary path is Claude (structured output). If no API credentials are
available the module falls back to a keyword heuristic, clearly labelled
as such in the result.
"""

from __future__ import annotations

import json
import os
import re

import anthropic

MODEL = os.environ.get("CITATOR_MODEL", "claude-opus-4-8")

# label -> (polarity, severity 0-3). Severity drives the overall status.
TREATMENTS: dict[str, tuple[str, int]] = {
    "overruled":     ("negative", 3),
    "not_followed":  ("negative", 3),
    "departed_from": ("negative", 3),
    "doubted":       ("negative", 2),
    "criticised":    ("negative", 2),
    "distinguished": ("caution",  1),
    "applied":       ("positive", 0),
    "followed":      ("positive", 0),
    "approved":      ("positive", 0),
    "considered":    ("neutral",  0),
    "cited":         ("neutral",  0),
    "unclear":       ("neutral",  0),
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "treatment": {"type": "string", "enum": sorted(TREATMENTS)},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence_quote": {
            "type": "string",
            "description": "Verbatim quote from the passages that best shows the treatment",
        },
        "explanation": {"type": "string"},
    },
    "required": ["treatment", "confidence", "evidence_quote", "explanation"],
    "additionalProperties": False,
}

_SYSTEM = """You are a legal citator analyst for judgments of the courts of England and Wales.
You are given passages from a LATER judgment that cite an EARLIER target case.
Classify how the later judgment treats the target case, using exactly one label:

overruled — the later court holds the target case was wrongly decided and overrules it
not_followed / departed_from — the court declines to follow the target case
doubted / criticised — the court questions or criticises the target case's correctness
distinguished — the court treats the target as good law but inapplicable on the facts
applied / followed / approved — the court relies on or endorses the target case
considered — the court discusses the target case without clearly relying on or rejecting it
cited — a bare citation with no substantive discussion
unclear — the passages are insufficient to tell

Rules:
- Classify only the treatment of the TARGET case, not other cases mentioned.
- A citation appearing in a quotation from another case, in a list of authorities,
  or in counsel's argument (not the court's reasoning) is at most "cited".
- Only use overruled/not_followed when the court itself says so of the target case.
- evidence_quote must be copied verbatim from the passages."""


def classify_with_claude(client: anthropic.Anthropic, target_citation: str,
                         target_name: str | None, citing_name: str | None,
                         contexts: list[str]) -> dict:
    target_desc = f"{target_name} {target_citation}" if target_name else target_citation
    passages = "\n\n---\n\n".join(contexts)
    user = (
        f"Target case: {target_desc}\n"
        f"Citing judgment: {citing_name or 'unknown'}\n\n"
        f"Passages from the citing judgment:\n\n{passages}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        return _result("unclear", "low", "", "Model declined to classify this passage.",
                       method="claude")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    return _result(data["treatment"], data["confidence"], data["evidence_quote"],
                   data["explanation"], method="claude")


_NEGATIVE_PATTERNS = [
    (re.compile(r"\boverrul", re.I), "overruled"),
    (re.compile(r"\bwrongly decided\b", re.I), "overruled"),
    (re.compile(r"\b(decline[sd]?|refus\w+) to follow\b", re.I), "not_followed"),
    (re.compile(r"\bnot follow(ed)?\b", re.I), "not_followed"),
    (re.compile(r"\bdepart(ed|ing)? from\b", re.I), "departed_from"),
    (re.compile(r"\bdoubt(ed|s|ing)?\b", re.I), "doubted"),
    (re.compile(r"\bcriticis", re.I), "criticised"),
    (re.compile(r"\bdistinguish", re.I), "distinguished"),
    (re.compile(r"\bapplied\b|\bapplying\b", re.I), "applied"),
    (re.compile(r"\bfollow(ed|ing)\b", re.I), "followed"),
    (re.compile(r"\bapprov(ed|ing)\b", re.I), "approved"),
    (re.compile(r"\bconsider(ed|ing)\b", re.I), "considered"),
]


def classify_heuristic(contexts: list[str]) -> dict:
    """Crude keyword fallback used only when the Claude API is unavailable."""
    text = "\n".join(contexts)
    for pattern, label in _NEGATIVE_PATTERNS:
        m = pattern.search(text)
        if m:
            start = max(0, m.start() - 120)
            snippet = text[start:m.end() + 120].strip()
            return _result(label, "low", snippet,
                           f"Keyword match ({m.group(0)!r}); verify by reading the judgment.",
                           method="heuristic")
    return _result("cited", "low", "", "No treatment keywords found near the citation.",
                   method="heuristic")


def _result(treatment: str, confidence: str, quote: str, explanation: str,
            method: str) -> dict:
    polarity, severity = TREATMENTS.get(treatment, ("neutral", 0))
    return {
        "treatment": treatment,
        "polarity": polarity,
        "severity": severity,
        "confidence": confidence,
        "evidence_quote": quote,
        "explanation": explanation,
        "method": method,
    }


def make_client() -> anthropic.Anthropic:
    """Anthropic client; credentials resolve from the environment or an
    `ant auth login` profile. Auth failures surface on first use and the
    pipeline falls back to the keyword heuristic."""
    return anthropic.Anthropic()
