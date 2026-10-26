#!/usr/bin/env bash
# End-to-end test with no network and no API key: a synthetic dataset served by a
# fake Hugging Face Hub, a fake Messages API, prepare.py, then the projector and
# camera pages driven in headless Chromium (live, fallback and --offline paths).
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
export ANTHROPIC_BASE_URL=http://127.0.0.1:8765 ANTHROPIC_API_KEY=fake FAKE_LOG="$WORK/requests.jsonl"
$PY prepare.py | tee "$WORK/prepare.log"
$PY prepare.py | grep -q "All answers already cached"   # resumable: nothing redone

$PY - "$WORK" <<'PYEOF'
import collections, json, sys
work = sys.argv[1]
tasks = json.load(open(f"{work}/data/shortlist.json"))["tasks"]
kinds = collections.Counter(t["media_kind"] for t in tasks)
assert kinds == {"image": 20, "video": 15}, kinds
assert all(t["duration"] < 45 for t in tasks if t["media_kind"] == "video")
per_domain = collections.Counter(t["domain"] for t in tasks)
assert max(per_domain.values()) - min(per_domain.values()) <= 2, per_domain
assert all(t["credit"] for t in tasks)
cache = json.load(open(f"{work}/data/cache/answers.json"))
assert set(cache) == {t["task_id"] for t in tasks}
assert all("judgment" in e for e in cache.values())
reqs = [json.loads(l) for l in open(f"{work}/requests.jsonl")]
answers = [r for r in reqs if not r["system"]]
assert all(r["model"] == "claude-opus-5-5" and r["effort"] == "xhigh" for r in answers)
for t in tasks:
    if t["media_kind"] == "video":
        r = next(r for r in answers if r["frame_labels"] and f"duration {t['duration']:.2f}s" in r["first_text"])
        assert r["frame_labels"] == r["images"] == int(t["duration"] * 2), (t["task_id"], r["images"])
        assert r["first_text"].startswith("The video below is provided as")
    assert any(r["last_text"] == f"Question: {t['prompt']}" for r in answers)
judges = [r for r in reqs if r["system"]]
assert all(r["model"] == "claude-opus-5" and r["effort"] == "high" for r in judges)
print(f"prepare checks passed: {dict(kinds)}, {dict(per_domain)}")
PYEOF

$PY tests/e2e_ui.py "$WORK/data" "$WORK/screenshots"
echo "Screenshots: $WORK/screenshots"
