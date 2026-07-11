"""Table-of-contents extraction.

Two sources, tried in this order:

1. **Bookmarks** — the PDF's embedded outline (``doc.get_toc()``). Page
   numbers are exact, nesting is explicit. Best case.
2. **Printed index** — a table of contents typeset on the first pages of the
   document (common in court bundles, board packs, discovery productions).
   Parsed heuristically from word positions: a TOC line is text followed by
   dot leaders or a wide gap and a trailing page number. Nesting comes from
   decimal numbering ("3.1.2") when present, otherwise from indentation.

Printed page numbers refer to the *bundle's* pagination, which may not match
PDF page indices (cover sheets, the index itself). ``offset`` maps between
them: printed page ``p`` lives at PDF index ``p - 1 + offset``. We guess the
offset from PDF page labels when available; the UI lets the user correct it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


@dataclass
class Entry:
    """One line of the table of contents, flat form."""

    level: int  # 1-based nesting depth
    title: str
    start: int  # raw page reference: printed number (index) or 1-based PDF page (bookmarks)
    end: int | None = None  # same space as start; filled in by compute_ends


@dataclass
class Toc:
    source: str  # "bookmarks" | "index"
    entries: list[Entry]
    offset: int = 0  # printed page p -> PDF index p - 1 + offset (index source only)
    index_pages: tuple[int, int] | None = None  # PDF index range of the printed TOC itself


# --------------------------------------------------------------------------
# Bookmarks
# --------------------------------------------------------------------------

def from_bookmarks(doc: fitz.Document) -> Toc | None:
    raw = doc.get_toc(simple=True)
    entries = [
        Entry(level=max(1, lvl), title=title.strip() or "Untitled", start=page)
        for lvl, title, page in raw
        if page >= 1
    ]
    if not entries:
        return None
    compute_ends(entries, last_page=doc.page_count)
    return Toc(source="bookmarks", entries=entries)


# --------------------------------------------------------------------------
# Printed index
# --------------------------------------------------------------------------

_LEADER_CHARS = ".·•…_"
_PAGE_TOKEN = re.compile(r"^\(?(\d{1,5})(?:\s*[-–—‒]\s*(\d{1,5}))?\)?[.,]?$")
_DASH_TOKEN = re.compile(r"^[-–—‒]$")
_NUMBERING = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)[.)]?\s+")
_MIN_GAP_PT = 12  # horizontal gap that separates title text from a page column


def _lines_of(page: fitz.Page) -> list[list[tuple]]:
    """Group the page's words into visual lines, each sorted left-to-right.

    Grouping is by vertical position rather than the PDF's own block/line
    structure: TOCs are often typeset as tables, where the title and the page
    number live in different blocks despite sitting on the same visual line.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    lines: list[list] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        mid = (w[1] + w[3]) / 2
        if lines and abs(mid - lines[-1][0]) <= 3:
            lines[-1][1].append(w)
        else:
            lines.append([mid, [w]])
    return [sorted(ws, key=lambda w: w[0]) for _mid, ws in lines]


def _parse_line(line: list[tuple]) -> tuple[str, float, int, int] | None:
    """If this looks like a TOC line, return (title, indent_x0, start, end)."""
    if len(line) < 2:
        return None

    # Peel page reference tokens off the right edge: "45", "45-47", or "45 – 47".
    words = list(line)
    m = _PAGE_TOKEN.match(words[-1][4])
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1))
    page_x0 = words[-1][0]
    words.pop()
    if len(words) >= 2 and _DASH_TOKEN.match(words[-1][4]):
        m2 = _PAGE_TOKEN.match(words[-2][4])
        if m2 and not m2.group(2):
            end = start
            start = int(m2.group(1))
            page_x0 = words[-2][0]
            words = words[:-2]
    if not words:
        return None

    # The page number must be visually separated from the title: a wide gap
    # or a run of dot leaders. Otherwise "Meeting of 12" would match.
    prev = words[-1]
    gap = page_x0 - prev[2]
    prev_is_leader = set(prev[4]) <= set(_LEADER_CHARS)
    title_words = words[:-1] if prev_is_leader else words
    if not title_words:
        return None
    raw_title = " ".join(w[4] for w in title_words).strip()
    has_leaders = prev_is_leader or raw_title.endswith("..")
    if gap < _MIN_GAP_PT and not has_leaders:
        return None
    title = raw_title.rstrip(_LEADER_CHARS + " -–—")
    if not re.search(r"[A-Za-z]", title):
        return None
    if end < start:
        end = start
    return title, title_words[0][0] if title_words else prev[0], start, end


def from_printed_index(doc: fitz.Document, max_scan: int = 50) -> Toc | None:
    """Find the first run of index-looking pages and parse them."""
    scan = min(max_scan, doc.page_count)
    per_page: list[list[tuple[str, float, int, int]]] = []
    for pno in range(scan):
        hits = []
        for line in _lines_of(doc[pno]):
            parsed = _parse_line(line)
            if parsed:
                hits.append(parsed)
        per_page.append(hits)

    # First contiguous run of pages with >= 4 TOC-shaped lines each.
    first = next((i for i, h in enumerate(per_page) if len(h) >= 4), None)
    if first is None:
        return None
    last = first
    while last + 1 < scan and len(per_page[last + 1]) >= 4:
        last += 1

    rows = [r for p in range(first, last + 1) for r in per_page[p]]
    if not rows:
        return None

    levels = _assign_levels(rows)
    entries = [
        Entry(level=lv, title=t, start=s, end=e if e > s else None)
        for (t, _x, s, e), lv in zip(rows, levels)
    ]
    offset = _guess_offset(doc, entries, index_last=last)
    compute_ends(entries, last_page=max(1, doc.page_count - offset))
    return Toc(source="index", entries=entries, offset=offset, index_pages=(first, last))


def _assign_levels(rows: list[tuple[str, float, int, int]]) -> list[int]:
    """Nesting from decimal numbering if most rows carry it, else indentation."""
    numbered = [_NUMBERING.match(t) for t, _x, _s, _e in rows]
    n_with = sum(1 for m in numbered if m)
    depths = {m.group(1).count(".") + 1 for m in numbered if m}
    if n_with >= 0.6 * len(rows) and len(depths) > 1:
        return [m.group(1).count(".") + 1 if m else 1 for m in numbered]

    # Cluster distinct left edges; each cluster is a nesting level.
    xs = sorted({round(x) for _t, x, _s, _e in rows})
    clusters: list[list[int]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 8:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    edges = [c[0] for c in clusters]

    def level_of(x: float) -> int:
        for i in range(len(edges) - 1, -1, -1):
            if x >= edges[i] - 4:
                return min(i + 1, 6)
        return 1

    return [level_of(x) for _t, x, _s, _e in rows]


def _guess_offset(doc: fitz.Document, entries: list[Entry], index_last: int) -> int:
    """Best guess for printed-page -> PDF-index offset."""
    max_printed = max(e.start for e in entries)

    # PDF page labels are authoritative when present.
    try:
        labelled = doc.get_page_numbers("1", only_one=True)
    except Exception:
        labelled = []
    if labelled and max_printed - 1 + labelled[0] < doc.page_count:
        return labelled[0]

    # If printed page 1 would land inside the index itself, the bundle was
    # probably paginated starting after the front matter.
    first_printed = entries[0].start
    if first_printed - 1 <= index_last:
        guess = index_last + 1 - (first_printed - 1)
        if max_printed - 1 + guess < doc.page_count:
            return guess
    return 0


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def compute_ends(entries: list[Entry], last_page: int) -> None:
    """Fill in end pages: each entry runs until the next entry begins."""
    for i, e in enumerate(entries):
        if e.end is not None:
            continue  # the index gave an explicit range
        if i + 1 < len(entries):
            e.end = max(e.start, entries[i + 1].start - 1)
        else:
            e.end = max(e.start, last_page)


def to_tree(entries: list[Entry]) -> list[dict]:
    """Nest a flat, ordered entry list into a tree of dicts."""
    root: list[dict] = []
    stack: list[dict] = []
    for e in entries:
        node = {"title": e.title, "level": e.level, "start": e.start, "end": e.end, "children": []}
        while stack and stack[-1]["level"] >= e.level:
            stack.pop()
        (stack[-1]["children"] if stack else root).append(node)
        stack.append(node)
    return root


def extract(doc: fitz.Document) -> dict[str, Toc]:
    """All available TOC sources for this document."""
    out = {}
    bm = from_bookmarks(doc)
    if bm:
        out["bookmarks"] = bm
    idx = from_printed_index(doc)
    if idx:
        out["index"] = idx
    return out
