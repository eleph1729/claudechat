"""Entry points.

    python -m splitting_headache                     # launch the GUI (opens browser)
    python -m splitting_headache bundle.pdf -o out/  # headless split, no server
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="splitting_headache",
        description="Split a long indexed PDF into folders of individual documents.",
    )
    ap.add_argument("pdf", nargs="?", help="PDF to split headlessly; omit to launch the GUI")
    ap.add_argument("-o", "--out", default=".", help="output directory (headless mode)")
    ap.add_argument("--source", choices=["auto", "bookmarks", "index"], default="auto",
                    help="which table of contents to use")
    ap.add_argument("--offset", type=int, default=None,
                    help="printed page 1 of the index sits at PDF page 1+OFFSET (index source)")
    ap.add_argument("--port", type=int, default=8471, help="GUI port")
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    args = ap.parse_args()

    if args.pdf is None:
        from .app import main as run_app

        run_app(port=args.port, open_browser=not args.no_browser)
        return 0

    import os

    import fitz

    from . import toc as toc_mod
    from .splitter import split_to_dir

    doc = fitz.open(args.pdf)
    with doc:
        sources = toc_mod.extract(doc)
        if not sources:
            print("No table of contents found (no bookmarks, no printed index).", file=sys.stderr)
            return 1
        source = args.source
        if source == "auto":
            source = "bookmarks" if "bookmarks" in sources else "index"
        if source not in sources:
            print(f"No {source} TOC in this PDF (available: {', '.join(sources)}).", file=sys.stderr)
            return 1
        t = sources[source]
        offset = t.offset if args.offset is None else args.offset
        stem = os.path.splitext(os.path.basename(args.pdf))[0]
        result = split_to_dir(
            doc, t.entries, args.out, f"{stem} — split",
            offset=offset if source == "index" else 0,
            printed_pages=(source == "index"),
        )
    print(f"Wrote {result.documents} documents in {result.folders} folders "
          f"to {os.path.join(args.out, stem + ' — split')} (source: {source})")
    if result.skipped:
        print(f"Skipped {len(result.skipped)} entries outside the PDF — check --offset:",
              file=sys.stderr)
        for title in result.skipped[:10]:
            print(f"  - {title}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
