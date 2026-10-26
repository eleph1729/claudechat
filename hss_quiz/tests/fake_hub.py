"""Serve a local dataset directory the way the Hugging Face Hub does, for testing.

    python tests/fake_hub.py 8766 /tmp/hss-fixture
    HF_ENDPOINT=http://127.0.0.1:8766 HF_TOKEN=hf_fake python prepare.py

Implements just what prepare.py touches: the repo tree listing and
`resolve/main/<file>` (with a redirect to a "CDN" host that rejects auth headers, like the real Hub,
and HTTP range support so ffprobe can read durations without downloading).
Every request must carry the bearer token, as for a gated dataset; requests
are logged to <root>/../hub_requests.log so tests can count media downloads.
"""

import hashlib
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

ROOT = Path(sys.argv[2]).resolve()
LOG = ROOT.parent / "hub_requests.log"
COMMIT = "0" * 40
PORT = int(sys.argv[1])
app = FastAPI()


def log(req: Request, note: str = ""):
    with open(LOG, "a") as fh:
        fh.write(f"{req.method} {req.url.path} {req.headers.get('range', '')} {note}\n")


def check_auth(req: Request):
    if req.headers.get("authorization") != "Bearer hf_fake":
        raise HTTPException(401, "gated")


@app.get("/api/datasets/{org}/{name}/tree/{rev}")
@app.get("/api/datasets/{org}/{name}/tree/{rev}/{path:path}")
def tree(org: str, name: str, rev: str, request: Request, path: str = ""):
    check_auth(request)
    log(request)
    out = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(ROOT))
            out.append({"type": "file", "oid": hashlib.sha1(rel.encode()).hexdigest(),
                        "size": p.stat().st_size, "path": rel})
    return JSONResponse(out)


def _headers(p: Path):
    etag = hashlib.sha1(f"{p}{p.stat().st_size}".encode()).hexdigest()
    return {"X-Repo-Commit": COMMIT, "ETag": f'"{etag}"', "Content-Length": str(p.stat().st_size),
            "Accept-Ranges": "bytes"}


@app.api_route("/datasets/{org}/{name}/resolve/{rev}/{path:path}", methods=["GET", "HEAD"])
def resolve(org: str, name: str, rev: str, path: str, request: Request):
    check_auth(request)
    p = ROOT / path
    if not p.is_file():
        raise HTTPException(404, headers={"X-Error-Code": "EntryNotFound"})
    log(request)
    # Like the Hub: media redirect to a signed CDN URL that needs no auth header.
    headers = {k: v for k, v in _headers(p).items() if k != "Content-Length"}
    return RedirectResponse(f"http://localhost:{PORT}/cdn/{path}?sig=ok", status_code=302,
                            headers=headers)


@app.api_route("/cdn/{path:path}", methods=["GET", "HEAD"])
def cdn(path: str, request: Request):
    p = ROOT / path
    # Like a pre-signed S3 URL: a second auth mechanism is an error.
    if request.headers.get("authorization"):
        raise HTTPException(400, "only one auth mechanism allowed")
    if request.query_params.get("sig") != "ok" or not p.is_file():
        raise HTTPException(403)
    log(request, "cdn")
    if request.method == "HEAD":
        return Response(headers=_headers(p))
    return FileResponse(p, headers={"ETag": _headers(p)["ETag"], "X-Repo-Commit": COMMIT})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
