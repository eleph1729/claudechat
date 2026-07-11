"""Split a PDF into nested folders of individual documents.

The TOC tree maps onto the filesystem: entries with children become folders,
leaf entries become PDFs. Every filename carries a cross-reference to where
the document sits in the original bundle, e.g.::

    03. Witness Statement of A. Nother [pp 214-231].pdf

If a parent entry starts before its first child (a tab divider or section
cover sheet), those pages are preserved as ``00. <title> (cover) [...]`` so
no page of the original is lost.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass

import fitz

from .toc import Entry, compute_ends, to_tree

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_NAME = 110  # per path component, leaving room for numbering + page suffix


def sanitize(name: str) -> str:
    name = _ILLEGAL.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > _MAX_NAME:
        name = name[: _MAX_NAME - 1].rstrip() + "…"
    return name or "Untitled"


def page_ref(start: int, end: int) -> str:
    """1-based page range of the original, for filenames."""
    return f"[p {start}]" if start == end else f"[pp {start}-{end}]"


@dataclass
class SplitResult:
    documents: int
    folders: int
    skipped: list[str]


class _ZipSink:
    """Writes split output straight into a zip archive."""

    def __init__(self, zf: zipfile.ZipFile, root: str):
        self.zf = zf
        self.root = root

    def write(self, parts: list[str], data: bytes) -> str:
        path = "/".join([self.root, *parts])
        self.zf.writestr(path, data)
        return path


class _DirSink:
    """Writes split output into a directory tree on disk."""

    def __init__(self, out_dir: str, root: str):
        self.base = os.path.join(out_dir, root)

    def write(self, parts: list[str], data: bytes) -> str:
        path = os.path.join(self.base, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return os.path.relpath(path, self.base)


def _prepare(
    entries: list[Entry], page_count: int, offset: int, printed: bool
) -> tuple[list[Entry], list[str]]:
    """Convert raw entries to 0-based PDF page spans, dropping out-of-range ones."""
    out: list[Entry] = []
    skipped: list[str] = []
    for e in entries:
        start = e.start - 1 + (offset if printed else 0)
        if not 0 <= start < page_count:
            skipped.append(e.title)
            continue
        end = None
        if e.end is not None and e.end != e.start:
            end = min(max(start, e.end - 1 + (offset if printed else 0)), page_count - 1)
        out.append(Entry(level=e.level, title=e.title, start=start, end=end))
    compute_ends(out, last_page=page_count - 1)
    for e in out:
        e.end = min(e.end, page_count - 1)
    return out, skipped


def split(
    src: fitz.Document,
    entries: list[Entry],
    sink,
    *,
    offset: int = 0,
    printed_pages: bool = False,
    manifest: bool = True,
) -> SplitResult:
    prepared, skipped = _prepare(entries, src.page_count, offset, printed_pages)
    tree = to_tree(prepared)

    rows: list[list] = []
    counts = {"docs": 0, "dirs": 0}

    def emit_pdf(parts: list[str], title: str, start: int, end: int) -> None:
        sub = fitz.open()
        sub.insert_pdf(src, from_page=start, to_page=end)
        data = sub.tobytes(garbage=1, deflate=True)
        sub.close()
        path = sink.write(parts, data)
        rows.append([path, title, start + 1, end + 1, end - start + 1])
        counts["docs"] += 1

    def unique(names: set[str], name: str) -> str:
        base, n = name, 2
        stem, ext = os.path.splitext(base)
        while name.lower() in names:
            name = f"{stem} ({n}){ext}"
            n += 1
        names.add(name.lower())
        return name

    def walk(nodes: list[dict], prefix: list[str]) -> None:
        width = max(2, len(str(len(nodes))))
        names: set[str] = set()
        for i, node in enumerate(nodes, 1):
            seq = str(i).zfill(width)
            title = sanitize(node["title"])
            if node["children"]:
                folder = unique(names, f"{seq}. {title}")
                counts["dirs"] += 1
                first_child = node["children"][0]["start"]
                if first_child > node["start"]:
                    emit_pdf(
                        [*prefix, folder, f"00. {title} (cover) {page_ref(node['start'] + 1, first_child)}.pdf"],
                        f"{node['title']} (cover)",
                        node["start"],
                        first_child - 1,
                    )
                walk(node["children"], [*prefix, folder])
            else:
                ref = page_ref(node["start"] + 1, node["end"] + 1)
                fname = unique(names, f"{seq}. {title} {ref}.pdf")
                emit_pdf([*prefix, fname], node["title"], node["start"], node["end"])

    walk(tree, [])

    if manifest and rows:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["path", "title", "first_page", "last_page", "pages"])
        w.writerows(rows)
        sink.write(["manifest.csv"], buf.getvalue().encode("utf-8"))

    return SplitResult(documents=counts["docs"], folders=counts["dirs"], skipped=skipped)


def split_to_zip(src: fitz.Document, entries: list[Entry], fp, root: str, **kw) -> SplitResult:
    with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        return split(src, entries, _ZipSink(zf, sanitize(root)), **kw)


def split_to_dir(src: fitz.Document, entries: list[Entry], out_dir: str, root: str, **kw) -> SplitResult:
    return split(src, entries, _DirSink(out_dir, sanitize(root)), **kw)
