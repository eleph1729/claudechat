"""Local web app: upload a bundle PDF, preview its TOC, download the split zip."""

from __future__ import annotations

import os
import tempfile
import time
import uuid

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file

from . import toc as toc_mod
from .splitter import split_to_zip

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "splitting_headache")
SESSION_TTL = 4 * 3600  # seconds before an uploaded bundle is reaped

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024**3  # bundles can be enormous


def _session_path(token: str) -> str:
    if not token.isalnum() or len(token) != 32:
        abort(400, "bad token")
    return os.path.join(UPLOAD_DIR, f"{token}.pdf")


def _reap_old_uploads() -> None:
    now = time.time()
    try:
        for name in os.listdir(UPLOAD_DIR):
            path = os.path.join(UPLOAD_DIR, name)
            if now - os.path.getmtime(path) > SESSION_TTL:
                os.unlink(path)
    except OSError:
        pass


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze():
    f = request.files.get("pdf")
    if f is None or not f.filename:
        return jsonify(error="No file received."), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _reap_old_uploads()
    token = uuid.uuid4().hex
    path = _session_path(token)
    f.save(path)

    try:
        doc = fitz.open(path)
    except Exception:
        os.unlink(path)
        return jsonify(error="That file doesn't look like a readable PDF."), 400

    with doc:
        sources = toc_mod.extract(doc)
        payload = {
            "token": token,
            "filename": f.filename,
            "pages": doc.page_count,
            "sources": {},
        }
        for name, t in sources.items():
            payload["sources"][name] = {
                "entries": toc_mod.to_tree(t.entries),
                "count": len(t.entries),
                "offset": t.offset,
                "index_pages": t.index_pages,
            }
    if not sources:
        payload["error"] = (
            "No table of contents found — the PDF has no bookmarks and no "
            "recognisable printed index in its first 50 pages."
        )
    return jsonify(payload)


@app.post("/api/split")
def do_split():
    data = request.get_json(force=True, silent=True) or {}
    token = str(data.get("token", ""))
    source = data.get("source")
    if source not in ("bookmarks", "index"):
        return jsonify(error="Unknown TOC source."), 400
    try:
        offset = int(data.get("offset", 0))
    except (TypeError, ValueError):
        return jsonify(error="Offset must be a whole number."), 400

    path = _session_path(token)
    if not os.path.exists(path):
        return jsonify(error="Upload expired — please upload the PDF again."), 410

    doc = fitz.open(path)
    with doc:
        sources = toc_mod.extract(doc)
        if source not in sources:
            return jsonify(error=f"No {source} TOC in this PDF."), 400
        t = sources[source]

        stem = os.path.splitext(str(data.get("filename") or "bundle"))[0]
        tmp = tempfile.SpooledTemporaryFile(max_size=256 * 1024**2, dir=UPLOAD_DIR)
        result = split_to_zip(
            doc,
            t.entries,
            tmp,
            root=f"{stem} — split",
            offset=offset if source == "index" else 0,
            printed_pages=(source == "index"),
        )

    if result.documents == 0:
        tmp.close()
        return jsonify(error="No documents fell inside the PDF at that page offset."), 400

    tmp.seek(0)
    resp = send_file(
        tmp,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{stem} — split.zip",
    )
    resp.headers["X-Split-Documents"] = str(result.documents)
    resp.headers["X-Split-Folders"] = str(result.folders)
    resp.headers["X-Split-Skipped"] = str(len(result.skipped))
    return resp


def main(port: int = 8471, open_browser: bool = True) -> None:
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, threaded=True)
