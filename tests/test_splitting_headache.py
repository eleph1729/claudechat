"""End-to-end tests against a synthetic bundle PDF built with PyMuPDF."""

import io
import zipfile

import fitz
import pytest

from splitting_headache import toc as toc_mod
from splitting_headache.app import app
from splitting_headache.splitter import sanitize, split_to_zip

# (level, title, printed_start). Parents own a 1-page divider before children.
STRUCTURE = [
    (1, "Section A - Pleadings", 1),
    (2, "Claim Form", 2),
    (2, "Particulars of Claim", 4),
    (1, "Section B - Witness Statements", 7),
    (2, "Witness Statement of Jane Roe", 8),
    (2, "Witness Statement of John Doe: the one with a very long title "
        "that keeps going and going to exercise filename truncation "
        "behaviour in the splitter / naming <code>", 11),
    (1, "Section C - Correspondence", 14),
    (2, "Letter dated 1 May 2024", 15),
    (2, "Email chain re: settlement", 17),
]
FRONT_MATTER = 2  # cover page + one printed index page before printed page 1


def leaders(n=40):
    return " " + "." * n + " "


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    doc = fitz.open()

    cover = doc.new_page()
    cover.insert_text((72, 200), "TRIAL BUNDLE", fontsize=28)

    idx = doc.new_page()
    idx.insert_text((72, 72), "INDEX", fontsize=16)
    y = 110
    for level, title, page in STRUCTURE:
        x = 72 + (level - 1) * 24
        short = title[:60]
        idx.insert_text((x, y), short, fontsize=10)
        idx.insert_text((500, y), str(page), fontsize=10)
        y += 18

    total_content = 18  # printed pages 1..18
    for p in range(1, total_content + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Content of printed page {p}", fontsize=12)

    bm = [[level, title, page + FRONT_MATTER] for level, title, page in STRUCTURE]
    doc.set_toc(bm)

    path = tmp_path_factory.mktemp("bundle") / "bundle.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_bookmarks_extraction(bundle):
    doc = fitz.open(bundle)
    t = toc_mod.from_bookmarks(doc)
    assert t is not None
    assert len(t.entries) == len(STRUCTURE)
    assert t.entries[0].title == "Section A - Pleadings"
    assert [e.level for e in t.entries] == [lvl for lvl, _, _ in STRUCTURE]
    doc.close()


def test_printed_index_extraction(bundle):
    doc = fitz.open(bundle)
    t = toc_mod.from_printed_index(doc)
    assert t is not None
    assert t.index_pages == (1, 1)
    assert len(t.entries) == len(STRUCTURE)
    assert [e.start for e in t.entries] == [p for _, _, p in STRUCTURE]
    assert [e.level for e in t.entries] == [lvl for lvl, _, _ in STRUCTURE]
    # index page 1 (0-based) + first printed page 1 -> offset = front matter
    assert t.offset == FRONT_MATTER
    doc.close()


def _check_zip(data: bytes, page_count: int):
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in zf.namelist() if n.endswith(".pdf")]
    # 6 leaf docs + 3 section covers (each section has a divider page)
    assert len(names) == 9
    dirs = {n.split("/")[1] for n in names}
    assert dirs == {
        "01. Section A - Pleadings",
        "02. Section B - Witness Statements",
        "03. Section C - Correspondence",
    }
    covers = [n for n in names if "(cover)" in n]
    assert len(covers) == 3
    # every filename cross-references a page range in the original
    assert all("[p" in n for n in names)
    assert any("manifest.csv" in n for n in zf.namelist())

    # page-count conservation: content pages of original == sum of split pages
    total = 0
    for n in names:
        sub = fitz.open(stream=zf.read(n), filetype="pdf")
        total += sub.page_count
        sub.close()
    assert total == page_count - FRONT_MATTER

    # spot-check a specific document's span: Claim Form printed pp 2-3
    claim = next(n for n in names if "Claim Form" in n)
    assert f"[pp {2 + FRONT_MATTER}-{3 + FRONT_MATTER}]" in claim


def test_split_from_bookmarks(bundle):
    doc = fitz.open(bundle)
    t = toc_mod.from_bookmarks(doc)
    buf = io.BytesIO()
    res = split_to_zip(doc, t.entries, buf, root="bundle — split")
    assert res.documents == 9 and res.folders == 3 and not res.skipped
    _check_zip(buf.getvalue(), doc.page_count)
    doc.close()


def test_split_from_printed_index(bundle):
    doc = fitz.open(bundle)
    t = toc_mod.from_printed_index(doc)
    buf = io.BytesIO()
    res = split_to_zip(doc, t.entries, buf, root="bundle — split",
                       offset=t.offset, printed_pages=True)
    assert res.documents == 9 and res.folders == 3 and not res.skipped
    _check_zip(buf.getvalue(), doc.page_count)
    doc.close()


def test_bad_offset_skips_entries(bundle):
    doc = fitz.open(bundle)
    t = toc_mod.from_printed_index(doc)
    buf = io.BytesIO()
    res = split_to_zip(doc, t.entries, buf, root="x",
                       offset=1000, printed_pages=True)
    assert res.documents == 0 and len(res.skipped) == len(STRUCTURE)
    doc.close()


def test_sanitize():
    assert sanitize('a<b>:"c/d\\e|f?g*') == "a b c d e f g"
    assert sanitize("   ") == "Untitled"
    assert len(sanitize("x" * 500)) <= 110


def test_web_roundtrip(bundle):
    client = app.test_client()
    with open(bundle, "rb") as f:
        r = client.post("/api/analyze", data={"pdf": (f, "bundle.pdf")})
    assert r.status_code == 200
    data = r.get_json()
    assert data["pages"] == 20
    assert set(data["sources"]) == {"bookmarks", "index"}
    assert data["sources"]["index"]["offset"] == FRONT_MATTER
    assert len(data["sources"]["bookmarks"]["entries"]) == 3  # 3 top-level sections

    r = client.post("/api/split", json={
        "token": data["token"], "filename": "bundle.pdf",
        "source": "index", "offset": FRONT_MATTER,
    })
    assert r.status_code == 200
    assert r.headers["X-Split-Documents"] == "9"
    _check_zip(r.data, 20)

    r = client.post("/api/split", json={"token": "0" * 32, "source": "index"})
    assert r.status_code == 410


def test_no_toc_pdf():
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "just one page, no index")
    data = doc.tobytes()
    doc.close()
    client = app.test_client()
    r = client.post("/api/analyze", data={"pdf": (io.BytesIO(data), "plain.pdf")})
    assert r.status_code == 200
    assert "error" in r.get_json()
