# HSS quiz show

A local, projector-friendly quiz built on **Humanity's Sixth Sense** (HSS), the
intuitive visual reasoning benchmark from Scale AI and Elorian
([arXiv 2610.08966](https://arxiv.org/abs/2610.08966), dataset
[`ScaleAI/HSS`](https://huggingface.co/datasets/ScaleAI/HSS)).

Questions are drawn at random from the whole dataset (522 tasks: images and
videos), leaving out videos longer than one minute. The audience writes down an answer while Claude answers the same
question live. Then the host reveals Claude's answer, the reference answer and
the rubric.

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

Start the show:

```bash
python server.py
```

Open http://localhost:8000 and press `F` for full screen.

Nothing is downloaded in advance. Each question's image or video is downloaded
when it's drawn, into the Hugging Face cache (`~/.cache/huggingface`), so a
question that comes up again later loads instantly. While a question is on
screen, the next random one downloads in the background.

## Host keys

| Key | Action |
| --- | --- |
| `Space` / `→` | next random question (`←` goes back; each question keeps its state) |
| `S` | skip this question |
| `V` | play the video now, without waiting for the countdown |
| `P` | pens down: stops the clock |
| `C` | reveal Claude's answer. If Claude is still thinking, a spinner shows how long it has taken so far, and the answer appears as soon as it arrives. If the call failed, the error is shown and pressing `C` again retries. |
| `R` | reveal the reference answer and rubric, with the judge's ✓/✗ per criterion and PASS/FAIL for Claude |
| `H` / `A` | point to Humans / to Claude (press again to undo) |
| `T` | show Claude's reasoning summary |
| `F` | full screen · `?` help · `Shift`+`R` restart |

**Videos.** Videos longer than 60 seconds are never shown (`--max-video 90`
changes the limit). The server reads each video's length from the Hub before
downloading it; ffprobe fetches only a few kilobytes, so long videos are passed
over without being downloaded. Lengths are remembered in `data/durations.json`,
so known long videos are skipped instantly in later shows.

A video's length shows in the top bar as soon as the question appears. The video
stays paused behind a 10-second countdown so everyone can read the question
first, then plays on a loop without sound (the benchmark doesn't use audio).
`--countdown` changes the delay.

**Claude starts when the question appears**, at the same time as the audience,
and gets the whole video at once as frames. The small status at the bottom right
says whether Claude is still thinking or has answered. Long videos take Claude
longer, and so does effort `xhigh`; `python server.py --effort high` answers
faster.

## Options

`--kind image` or `--kind video` (draw only one kind), `--max-video 60`,
`--countdown 10`, `--seed N` (repeatable order), `--effort LEVEL`,
`--model`, `--judge-model`, `--port 8000`, `--dataset PATH` (a local copy of the
dataset repo instead of the Hub).

## Fidelity to the paper

The request follows the paper's evaluation protocol. I couldn't open the paper
itself from the build environment. The prompts come from the authors' harness
([xingang2/HSS-Eval](https://github.com/xingang2/HSS-Eval)), whose README says
they reproduce Appendix C.6 verbatim:

- There is no system prompt. One user turn holds a one-line media preamble, the
  media, and `Question: <prompt>`.
- Images are sent at native resolution. They are downscaled only above 4096 px or
  4.5 MB.
- Videos are sent as frames sampled uniformly at **2 fps** with ffmpeg (capped at
  500), scaled to a longest side of
  768 px. Each frame is preceded by a `[frame t=1.50s]` label. The preamble gives
  the frame count, rate, duration and timestamps. There is no audio.
- The Claude API caps a request at 32 MB, so if a video's frames would exceed
  about 20 MB they are re-encoded smaller (the authors' harness does the same,
  with its own budget). Under a minute this rarely applies.
- Claude Opus 5.5 runs with adaptive thinking at effort `xhigh`, the paper's
  setting for this model.
- Grading uses the paper's judge prompt, a text-only judge (`claude-opus-5` at
  `high` effort, the paper's headline judge) that sees the question, reference
  answer, rubric and candidate answer. An answer passes only if **every**
  criterion is met.

Requests opt into server-side refusal fallbacks (`fallbacks: "default"`), so a
declined request is retried on Anthropic's recommended fallback model instead of
failing. The answer card shows which model actually answered.

## Media credits

The bottom line of each question shows the media's credit. The server looks for
credit, author, license and source columns in `test.jsonl` and for any credits or
attribution file in the dataset repo. If it finds neither, it credits the HSS
dataset and task ID and points to the dataset card's media notice. To set a credit
by hand, create `credits.json` here, mapping a task ID (or media path) to text:

```json
{"<task_id>": "Photo: Jane Doe, CC BY 4.0, via Wikimedia Commons"}
```

## Tests

`tests/run_e2e.sh` runs the whole flow without network access or an API key. It
uses a synthetic dataset in the HSS format (`tests/make_fixture.py`), a fake
Hugging Face Hub (`tests/fake_hub.py`) and a fake Messages API that logs what is
sent (`tests/fake_anthropic.py`). It drives the projector page in headless
Chromium and checks:

- random draws with no repeats, and the next question downloading in the
  background;
- Claude starting with each question;
- no video over a minute is ever shown or downloaded, and lengths are
  remembered;
- the video length badge, the countdown (video paused until it ends), `V`
  skipping it, and `S` skipping a question;
- reveals, scoring, a slow call, and a failed call retried with `C`;
- the prompt layout, the 2 fps frame counts, and the frame-size budget.

```bash
pip install -r requirements-test.txt && python -m playwright install chromium
bash tests/run_e2e.sh
```
