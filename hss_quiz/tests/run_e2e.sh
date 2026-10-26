#!/usr/bin/env bash
# End-to-end test with no network and no API key: a synthetic dataset served by a
# fake Hugging Face Hub, a fake Messages API, and the projector page driven in
# headless Chromium.
#
#   pip install -r requirements-test.txt && python -m playwright install chromium
#   bash tests/run_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/hss-e2e.XXXXXX")
cleanup() { kill "${HUB_PID:-}" "${API_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

$PY tests/make_fixture.py "$WORK/fixture"
$PY tests/fake_hub.py 8766 "$WORK/fixture" > "$WORK/hub.log" 2>&1 & HUB_PID=$!
$PY tests/fake_anthropic.py 8765 "$WORK/requests.jsonl" > "$WORK/api.log" 2>&1 & API_PID=$!
sleep 2

export HSS_DATA_DIR="$WORK/data" HF_HOME="$WORK/hf" HF_ENDPOINT=http://127.0.0.1:8766 HF_TOKEN=hf_fake
$PY tests/e2e_ui.py "$WORK/screenshots" "$WORK/hub_requests.log"

# What was sent to Claude: the paper's prompt layout, 2 fps frames for videos.
$PY - "$WORK" <<'PYEOF'
import json, sys
reqs = [json.loads(l) for l in open(f"{sys.argv[1]}/requests.jsonl")]
answers = [r for r in reqs if not r["system"]]
assert answers and all(r["model"] == "claude-opus-5-5" and r["effort"] == "xhigh" for r in answers)
assert all(r["last_text"].startswith("Question: ") for r in answers)
for r in answers:
    if r["frame_labels"]:
        dur = float(r["first_text"].split("video duration ")[1].split("s)")[0])
        assert r["frame_labels"] == r["images"] == int(dur * 2), (dur, r["images"])
judges = [r for r in reqs if r["system"]]
assert judges and all(r["model"] == "claude-opus-5" for r in judges)
print(f"request checks passed: {len(answers)} answers, {sum(bool(r['frame_labels']) for r in answers)} videos")
PYEOF

# A long video's frames are shrunk to keep the request under the API's 32 MB limit.
$PY - "$WORK" <<'PYEOF'
import json, sys
from pathlib import Path
import hss_common as hc
work = Path(sys.argv[1])
task = hc.Task("vid-009", "q", "media/videos/vid_009.mp4", "video", "", "", "", [])
task.local_path = str(work / "fixture" / task.media_path)
frames, *_ = hc.sample_frames(task.media_file, work / "frames" / task.task_id)
original = sum(p.stat().st_size for p in frames)
hc.FRAMES_MAX_TOTAL_BYTES = original // 3
content = hc.build_answer_content(task, work / "frames")
images = [b for b in content if b["type"] == "image"]
raw = sum(len(b["source"]["data"]) * 3 // 4 for b in images)
assert len(images) == 240 and raw <= hc.FRAMES_MAX_TOTAL_BYTES, (len(images), raw, original)
print(f"frame budget check passed: {len(images)} frames, {original} -> {raw} bytes")
PYEOF
echo "Screenshots: $WORK/screenshots"
