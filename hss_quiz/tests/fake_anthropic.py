"""A stand-in for the Messages API, for testing without a key or network.

    python tests/fake_anthropic.py 8765 /tmp/requests.jsonl
    ANTHROPIC_BASE_URL=http://127.0.0.1:8765 ANTHROPIC_API_KEY=fake python prepare.py ...

It streams SSE events in the API's wire format and logs a summary of every
request (model, effort, system prompt, image/frame counts, beta headers), so
the tests can check what the app actually sends. POST /control sets
{"delay": seconds, "fail": bool} for answer calls, to exercise the fallbacks.
"""

import asyncio
import json
import re
import sys
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
LOG = sys.argv[2] if len(sys.argv) > 2 else "requests.jsonl"
CONTROL = {"delay": 0.0, "fail": False}


@app.post("/control")
async def control(req: Request):
    CONTROL.update(await req.json())
    return CONTROL


def summarize(body, headers):
    msgs = body.get("messages", [])
    content = msgs[0]["content"] if msgs else []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    texts = [b["text"] for b in content if b.get("type") == "text"]
    return {
        "model": body.get("model"), "system": bool(body.get("system")),
        "effort": (body.get("output_config") or {}).get("effort"),
        "thinking": body.get("thinking"), "fallbacks": body.get("fallbacks"),
        "betas": headers.get("anthropic-beta"), "max_tokens": body.get("max_tokens"),
        "images": sum(1 for b in content if b.get("type") == "image"),
        "frame_labels": sum(1 for t in texts if t.startswith("[frame t=")),
        "first_text": texts[0] if texts else "", "last_text": texts[-1] if texts else "",
        "media_types": sorted({b["source"]["media_type"] for b in content if b.get("type") == "image"}),
        "n_messages": len(msgs),
    }


def reply_for(body, s):
    if s["system"]:  # the rubric judge
        user = body["messages"][0]["content"]
        cand = user.split("<candidate_answer>")[1].split("</candidate_answer>")[0]
        ids = re.findall(r"\[id: ([^\]]+)\] (.*)", user)
        crits = []
        for cid, title in ids:
            nums = re.findall(r"\d+", title)
            if "right to left" in title:
                met = "right to left" in cand
            else:
                met = bool(nums) and all(n in cand for n in nums)
            crits.append({"id": cid, "met": met, "justification": "fake judge"})
        return "", json.dumps({"criteria": crits, "overall_comment": "fake"})
    if "handwritten" in s["last_text"]:
        return "", "There are 4 circles"
    if s["frame_labels"]:
        return "Looking at the frames in order...", "The bar moves right to left."
    return "Counting the shapes...", "There are 4 circles."


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/v1/messages")
async def messages(req: Request):
    body = await req.json()
    s = summarize(body, req.headers)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(s) + "\n")
    is_answer = not s["system"] and "handwritten" not in s["last_text"]
    if is_answer and CONTROL["delay"]:
        await asyncio.sleep(CONTROL["delay"])
    if is_answer and CONTROL["fail"]:
        return JSONResponse({"type": "error", "error": {
            "type": "invalid_request_error", "message": "fake failure"}}, status_code=400)
    thinking, text = reply_for(body, s)

    def gen():
        yield sse("message_start", {"type": "message_start", "message": {
            "id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant",
            "model": body["model"], "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 1}}})
        i = 0
        if thinking:
            yield sse("content_block_start", {"type": "content_block_start", "index": i,
                      "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            yield sse("content_block_delta", {"type": "content_block_delta", "index": i,
                      "delta": {"type": "thinking_delta", "thinking": thinking}})
            yield sse("content_block_delta", {"type": "content_block_delta", "index": i,
                      "delta": {"type": "signature_delta", "signature": "fakesig"}})
            yield sse("content_block_stop", {"type": "content_block_stop", "index": i})
            i += 1
        yield sse("content_block_start", {"type": "content_block_start", "index": i,
                  "content_block": {"type": "text", "text": ""}})
        yield sse("content_block_delta", {"type": "content_block_delta", "index": i,
                  "delta": {"type": "text_delta", "text": text}})
        yield sse("content_block_stop", {"type": "content_block_stop", "index": i})
        yield sse("message_delta", {"type": "message_delta",
                  "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                  "usage": {"output_tokens": 42}})
        yield sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
