"""Parsing and normalisation of UK neutral citations.

A neutral citation like "[2015] EWCA Civ 123" maps to a Find Case Law
document URI ("ewca/civ/2015/123"), which is how the service addresses
judgments (https://caselaw.nationalarchives.gov.uk/{uri}/data.xml).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Courts whose division appears between the court code and the number:
#   [2015] EWCA Civ 123
_MID_DIVISIONS = {
    "EWCA": {"CIV": "civ", "CRIM": "crim"},
}

# Courts whose division appears in parentheses after the number:
#   [2015] EWHC 123 (Admin)
_TRAILING_DIVISIONS = {
    "EWHC": {
        "ADMIN": "admin", "ADMLTY": "admlty", "CH": "ch", "COMM": "comm",
        "COSTS": "costs", "FAM": "fam", "IPEC": "ipec", "KB": "kb",
        "MERCANTILE": "mercantile", "PAT": "pat", "QB": "qb", "SCCO": "scco",
        "TCC": "tcc",
    },
    "UKUT": {"AAC": "aac", "IAC": "iac", "LC": "lc", "TCC": "tcc"},
    "UKFTT": {"TC": "tc", "GRC": "grc"},
}

# Courts cited with no division: [2015] UKSC 11
_PLAIN_COURTS = {
    "UKSC": "uksc", "UKPC": "ukpc", "UKHL": "ukhl", "EWCOP": "ewcop",
    "EWFC": "ewfc", "EAT": "eat", "EWCC": "ewcc", "EWCR": "ewcr",
    "UKIPTRIB": "ukiptrib",
}

_CITATION_RE = re.compile(
    r"""\[\s*(?P<year>\d{4})\s*\]        # [2015]
        \s+(?P<court>[A-Za-z]+)          # EWCA / EWHC / UKSC ...
        (?:\s+(?P<mid>[A-Za-z]+))??      # Civ / Crim (optional, non-greedy)
        \s+(?P<sub>[A-Z])?(?P<num>\d+)   # 123, or B45 (EWFC sub-division)
        (?:\s*\(\s*(?P<trail>[A-Za-z]+)\s*\))?  # (Admin) / (IAC) ...
    """,
    re.VERBOSE,
)


class CitationError(ValueError):
    """Raised when a string cannot be parsed as a supported neutral citation."""


@dataclass(frozen=True)
class NeutralCitation:
    year: int
    court: str          # canonical court code, e.g. "EWCA"
    division: str | None  # canonical division token as cited, e.g. "Civ", "Admin"
    number: int
    subdivision: str | None = None  # e.g. "B" in [2021] EWFC B45

    @property
    def canonical(self) -> str:
        """The citation in its conventional printed form."""
        parts = [f"[{self.year}]", self.court]
        if self.court in _MID_DIVISIONS and self.division:
            parts.append(self.division)
        num = f"{self.subdivision}{self.number}" if self.subdivision else str(self.number)
        parts.append(num)
        if self.court in _TRAILING_DIVISIONS and self.division:
            parts[-1] = f"{num} ({self.division})"
        return " ".join(parts)

    @property
    def fcl_uri(self) -> str | None:
        """Find Case Law document URI, or None if the court isn't mapped."""
        if self.court in _MID_DIVISIONS:
            div = _MID_DIVISIONS[self.court].get((self.division or "").upper())
            return f"{_lower(self.court)}/{div}/{self.year}/{self.number}" if div else None
        if self.court in _TRAILING_DIVISIONS:
            div = _TRAILING_DIVISIONS[self.court].get((self.division or "").upper())
            return f"{_lower(self.court)}/{div}/{self.year}/{self.number}" if div else None
        if self.court in _PLAIN_COURTS:
            base = _PLAIN_COURTS[self.court]
            if self.subdivision:
                return f"{base}/{self.subdivision.lower()}/{self.year}/{self.number}"
            return f"{base}/{self.year}/{self.number}"
        return None

    def context_regex(self) -> re.Pattern:
        """Regex matching this citation in judgment text, tolerant of spacing."""
        bits = [r"\[\s*", str(self.year), r"\s*\]\s*", re.escape(self.court)]
        if self.court in _MID_DIVISIONS and self.division:
            bits += [r"\s+", re.escape(self.division)]
        bits += [r"\s+"]
        if self.subdivision:
            bits += [re.escape(self.subdivision), r"\s*"]
        bits += [str(self.number), r"\b"]
        return re.compile("".join(bits), re.IGNORECASE)


def _lower(court: str) -> str:
    return court.lower()


def parse_citation(text: str) -> NeutralCitation:
    """Parse a neutral citation string. Raises CitationError if unsupported."""
    m = _CITATION_RE.search(text.strip())
    if not m:
        raise CitationError(
            f"Could not parse {text!r} as a neutral citation "
            f"(expected something like '[2015] EWCA Civ 123')."
        )
    court = m.group("court").upper()
    mid, trail = m.group("mid"), m.group("trail")
    division = None
    if court in _MID_DIVISIONS:
        if not mid or mid.upper() not in _MID_DIVISIONS[court]:
            raise CitationError(f"{court} citations need a division (Civ or Crim): {text!r}")
        division = mid.capitalize()
    elif court in _TRAILING_DIVISIONS:
        token = trail or mid
        if not token or token.upper() not in _TRAILING_DIVISIONS[court]:
            known = ", ".join(sorted(_TRAILING_DIVISIONS[court]))
            raise CitationError(f"{court} citations need a division in brackets ({known}): {text!r}")
        division = token if token.isupper() else token.capitalize()
        if token.upper() in ("IAC", "LC", "TCC", "AAC", "TC", "GRC", "QB", "KB", "SCCO", "IPEC"):
            division = token.upper()
    elif court not in _PLAIN_COURTS:
        raise CitationError(f"Unrecognised court code {court!r} in {text!r}")

    return NeutralCitation(
        year=int(m.group("year")),
        court=court,
        division=division,
        number=int(m.group("num")),
        subdivision=m.group("sub"),
    )
