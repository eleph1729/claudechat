# HSS quiz show

A local, projector-friendly quiz built on **Humanity's Sixth Sense** (HSS), the
intuitive visual reasoning benchmark from Scale AI and Elorian
([arXiv 2610.08966](https://arxiv.org/abs/2610.08966), dataset
[`ScaleAI/HSS`](https://huggingface.co/datasets/ScaleAI/HSS)). The audience
writes down answers. Then the host reveals Claude's answer and the reference
answer with its rubric. Phones can photograph a handwritten answer, which
Claude reads and grades against the same rubric.

```
prepare.py:  task list ─► balanced shortlist ─► download that media only ─► Claude answers + rubric grades ─► data/cache
server.py:   projector page (/)  ◄── keyboard ──  host
             live Claude call per question, cached answer as fallback
             phone page (/camera) ─► photo ─► Claude reads handwriting ─► rubric judge
```

## Run it on macOS

Python 3.10 or newer is needed (the stock macOS `python3` may be older). First
get the code and set up a virtual environment:

```bash
brew install python@3.12 ffmpeg
git clone https://github.com/eleph1729/claudechat.git
cd claudechat
git checkout claude/happy-hopper-y4t0bg
cd hss_quiz
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

HSS is hosted on Hugging Face. Accept the dataset's terms at
https://huggingface.co/datasets/ScaleAI/HSS, then log in once and set your
Anthropic key:

```bash
hf auth login
export ANTHROPIC_API_KEY=sk-ant-...
```

Build the quiz (takes a few minutes; re-running resumes where it stopped), then
start the show:

```bash
python prepare.py
python server.py
```

Open http://localhost:8000 and press `F` for full screen.

`server.py` prints the phone URL and shows it as a QR code on the title screen.
Phones need to be on the same Wi-Fi as the Mac. The first time the server runs,
macOS may ask whether Python can accept incoming connections: allow it, or phones
can't connect.

No network at the venue? Run `prepare.py` beforehand, then use
`python server.py --offline`: it only shows cached answers and makes no API calls
(the camera page says it's unavailable).

## Host keys

| Key | Action |
| --- | --- |
| `Space` / `→` | next question (`←` goes back; each question keeps its state) |
| `P` | pens down: stops the clock |
| `C` | reveal Claude's answer. If the live call is still running, the page waits (up to `--live-grace`, 45 s by default) and then shows the cached answer. Press `C` again to skip the wait. |
| `R` | reveal the reference answer and rubric, with the judge's ✓/✗ per criterion for Claude and for each phone answer |
| `H` / `A` | point to Humans / to Claude (press again to undo) |
| `S` | skip question |
| `T` | show Claude's reasoning summary |
| `F` | full screen · `?` help · `Shift`+`R` restart |

The badge on Claude's answer says **LIVE** (answered just now) or **CACHED**
(recorded by `prepare.py`). If a live call failed and the cached answer is shown,
the page says so. The judge's PASS/FAIL is shown only after `R`, so it doesn't
give away the rubric.

## What prepare.py does

1. Downloads `data/test.jsonl` (the task list, without media) and prints its
   columns.
2. Picks about 20 images and 15 videos shorter than 45 s, balanced across the four
   HSS domains (round-robin over domains and their subdomains; `--seed` changes
   the pick). Video durations are read by `ffprobe` directly from the Hub, which
   fetches only a few byte ranges, so videos that are too long are never downloaded.
3. Downloads only the shortlisted media into `data/hss/`.
4. Asks `claude-opus-5-5` each question and grades each answer. Results go to
   `data/cache/answers.json`. Re-running only fills in what's missing;
   `--force` re-asks, `--reselect` picks a new shortlist.

Options: `--images N --videos N --max-video-seconds S --seed N --effort LEVEL
--workers N --skip-claude --no-judge --dataset PATH` (a local copy of the dataset
repo works too).

### Fidelity to the paper

The request follows the paper's evaluation protocol. I couldn't open the paper
itself from the build environment. The prompts come from the authors' harness
([xingang2/HSS-Eval](https://github.com/xingang2/HSS-Eval)), whose README says
they reproduce Appendix C.6 verbatim:

- There is no system prompt. One user turn holds a one-line media preamble, the
  media, and `Question: <prompt>`.
- Images are sent at native resolution. They are downscaled only above 4096 px or
  4.5 MB.
- Videos are sent as frames sampled uniformly at **2 fps** with ffmpeg (capped at
  500), scaled to a longest side of 768 px. Each frame is preceded by a
  `[frame t=1.50s]` label. The preamble gives the frame count, rate, duration and
  timestamps. There is no audio.
- Claude Opus 5.5 runs with adaptive thinking at effort `xhigh`, the paper's
  setting for this model.
- Grading uses the paper's judge prompt, a text-only judge (`claude-opus-5` at
  `high` effort, the paper's headline judge) that sees the question, reference
  answer, rubric and candidate answer. An answer passes only if **every**
  criterion is met.

The only addition is the handwriting reader for `/camera`. It transcribes the
photo with `claude-opus-5-5` and then grades the transcription with the same judge.

Requests opt into server-side refusal fallbacks (`fallbacks: "default"`), so a
declined request is retried on Anthropic's recommended fallback model instead of
failing. The answer card shows which model actually answered.

## Media credits

The bottom line of each question shows the media's credit. `prepare.py` looks for
credit, author, license and source columns in `test.jsonl` and for any
credits or attribution file in the dataset repo. If it finds neither, it credits
the HSS dataset and task ID and points to the dataset card's media notice. To set
a credit by hand, create `credits.json` here, mapping a task ID (or media path) to
text, and re-run `prepare.py --reselect`:

```json
{"<task_id>": "Photo: Jane Doe, CC BY 4.0, via Wikimedia Commons"}
```

Downloaded media, cached answers and audience photos stay in `data/`, which is
git-ignored. Audience photos are saved in `data/uploads/` and are only sent to the
Claude API for grading.

## Server options

`--offline`, `--port 8000`, `--host 0.0.0.0` (use `127.0.0.1` to keep phones out),
`--effort LEVEL` for live calls (`high` or `medium` answers faster than `xhigh`;
cached answers keep the effort they were made with), `--live-grace SECONDS`,
`--no-live-judge`.

## Tests

`tests/run_e2e.sh` runs the whole flow without network access or an API key. It
uses a synthetic dataset in the HSS format (`tests/make_fixture.py`), a fake
Hugging Face Hub (`tests/fake_hub.py`; it requires the token and redirects to a
"CDN" that rejects auth headers, like the real Hub) and a fake Messages API that
logs what is sent (`tests/fake_anthropic.py`). It checks the shortlist, the frame
counts and prompts sent to Claude, the cache, and then drives the projector and
phone pages in headless Chromium. That covers the live, slow-live, failed-live
and `--offline` paths, scoring, skipping, and a phone upload graded and revealed.

```bash
pip install -r requirements-test.txt && python -m playwright install chromium
bash tests/run_e2e.sh
```
