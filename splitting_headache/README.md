# Splitting Headache 💊

Feed it one giant PDF — a trial bundle, board pack, or discovery production
with 1000+ documents — and get back every document as its own file, arranged
in folders that mirror the table of contents, each filename cross-referenced
to its page range in the original.

## Quick start

```bash
pip install -r splitting_headache/requirements.txt
python -m splitting_headache          # opens the GUI at http://127.0.0.1:8471
```

Drop the PDF onto the page, check the preview tree, click **Split & download
zip**. Everything runs locally; the PDF never leaves your machine.

There's also a headless mode for scripting:

```bash
python -m splitting_headache bundle.pdf -o output/ [--source bookmarks|index] [--offset N]
```

## What you get

```
bundle — split/
├── manifest.csv                      # path, title, first/last page for every file
├── 01. Section A - Pleadings/
│   ├── 00. Section A - Pleadings (cover) [p 3].pdf
│   ├── 01. Claim Form [pp 4-5].pdf
│   └── 02. Particulars of Claim [pp 6-8].pdf
└── 02. Section B - Witness Statements/
    └── ...
```

- Folder nesting follows the TOC's nesting exactly; entries with sub-entries
  become folders, leaf entries become PDFs.
- Files are named as referenced in the index, prefixed with a sequence number
  so they sort in bundle order, with `[pp first-last]` giving the page range
  in the original PDF.
- Section divider/cover pages that precede a section's first document are
  kept as `00. … (cover)` files, so every page of the original is preserved.
- `manifest.csv` maps every output file back to its exact page span.

## How it reads the table of contents

1. **PDF bookmarks** (the embedded outline) when present — exact pages,
   explicit nesting.
2. **Printed index pages** otherwise — the parser scans the first 50 pages
   for TOC-shaped lines (title, dot leaders or a wide gap, page number),
   infers nesting from decimal numbering (`3.1.2`) or indentation, and
   guesses the printed-page → PDF-page offset from PDF page labels or the
   length of the front matter.

When both exist the GUI lets you switch between them, and for a printed
index you can nudge the **page offset** — the preview's page ranges update
live, so it's easy to see when they line up.

## Notes

- Split output streams straight into the zip; a 2,400-page bundle with 1,240
  documents splits in about a second.
- Uploads are held in the system temp directory and reaped after 4 hours.
- Tests: `python -m pytest tests/test_splitting_headache.py`.
