"""Flask app: a small web GUI over the citator pipeline.

Run with `python -m citator` and open http://127.0.0.1:5033/.
Progress streams to the browser over Server-Sent Events.
"""

from __future__ import annotations

import json

from flask import Flask, Response, render_template, request

from .pipeline import run_check

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/check")
def check():
    citation = (request.args.get("citation") or "").strip()
    case_name = (request.args.get("name") or "").strip() or None
    refresh = request.args.get("refresh") == "1"
    try:
        limit = max(1, min(100, int(request.args.get("limit", "25"))))
    except ValueError:
        limit = 25

    def stream():
        if not citation:
            yield _sse({"type": "error", "message": "Enter a neutral citation."})
            return
        for event in run_check(citation, case_name, limit=limit, refresh=refresh):
            yield _sse(event)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def main():
    app.run(host="127.0.0.1", port=5033, debug=False, threaded=True)


if __name__ == "__main__":
    main()
