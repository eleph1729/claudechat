# This repo

Two independent projects live here:

- **[Splitting Headache](splitting_headache/README.md)** — a local web app
  that splits a long indexed PDF (court bundle, board pack) into nested
  folders of individual documents named with their page ranges.
- **Panel Bot** (below) — a chatbot that listens to a live panel discussion.

# Panel Bot

A chatbot that listens to a live panel discussion and decides *when* — and
whether — to speak, rather than just taking strict turns. Built to run on a
MacBook Pro with audio in/out from the Mac.

## Pipeline

```
mic ──VAD──> utterances ──> Whisper STT ──> transcript ──> turn-taking ──> Claude (streamed) ──> TTS (ElevenLabs)
```

Each stage is its own small module so you can improve them independently:

| File | Job | Grow it into |
| --- | --- | --- |
| `panelbot/audio.py` | Mic capture + voice-activity detection | Real acoustic echo cancellation (see below) |
| `panelbot/stt.py` | Local speech-to-text (faster-whisper) | Streaming/partial transcripts |
| `panelbot/prosody.py` | Pitch/energy cues from each utterance | Richer features (speech rate, pause structure) |
| `panelbot/decide.py` | "Should I speak now?" — fast LLM speak/wait/yield | Learned classifier; interruption/barge-in |
| `panelbot/respond.py` | Generate the spoken reply (Claude), streamed as raw text deltas | Per-speaker memory, interruption awareness |
| `panelbot/tts.py` | Speak via ElevenLabs websocket streaming (required, no fallback) | Barge-in (cancel mid-utterance) |
| `panelbot/main.py` | The loop tying it together | |
| `config.py` | All the knobs (thresholds, model, voice) | |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # for the Claude reply step
export ELEVENLABS_API_KEY=...            # required: TTS has no fallback engine
python -m panelbot.main
```

First run downloads the Whisper model (`base.en`, ~150 MB). The microphone is
built into macOS; grant terminal mic permission when prompted. `ELEVENLABS_API_KEY`
is required — the bot raises immediately at startup if it's unset.

## How it decides to speak

`decide.py` has no hardcoded behavioral rules — no automatic "always answer if
addressed", no fixed cooldown timer. On a fresh remark, or the moment a real
lull opens up, a small fast model (`DECIDE_MODEL`, Haiku by default) weighs the
transcript, **prosody**, **timing**, and how recently the bot last spoke, and
returns `speak` / `wait` / `yield`. Only `speak` takes the floor.

Because the model reasons over the live transcript rather than fixed code
paths, it can pick up on ad-hoc instructions a human gives it mid-conversation
— "Claude, don't say anything until I say the word potato" or "give me a one
word answer" — without those cases being special-cased anywhere. The only
hardcoded logic left is a cost/latency gate (only bother calling the model
once per utterance or on a throttled re-check while silence continues, not
every loop tick) — that's an efficiency knob, not a behavioral rule.

**Gaps.** The bot is eager to fill a natural pause: once silence passes
`SILENCE_TO_SPEAK` it considers jumping in, and keeps re-considering every
`LULL_RECHECK_INTERVAL` so it can take a gap that opens a moment later — but
only while a *human* spoke last (if the bot spoke last, a following silence is
just nobody having replied yet, not an opening, so it won't monologue). Past
`LONG_SILENCE` it stops volunteering entirely: a very long pause usually means
the panel is busy with something off-mic, so it waits for them to resume
rather than break the silence. The model is also told this nuance directly, so
its judgment and the timing gate agree.

The prosody (`prosody.py`) is the key new signal. From each utterance's audio we
extract cheap cues — final pitch trend (rising = a question/invitation, falling =
a settled thought), trailing energy (winding down vs. ending strong), and
duration — and describe them in words for the model. Falling intonation + energy
trailing off + a genuine silence pushes toward `speak`; rising or steady delivery
pushes toward `wait`/`yield`.

The decision runs at most about once per utterance (not every loop tick), uses a
small model for low latency, and prints its call as `[decide] speak: ...` so you
can watch it and feel where it's wrong. Set `USE_AI_DECISION = False` in
`config.py` to fall back to the pure silence rule (also the automatic fallback if
the API call errors). Tune thresholds and the model in `config.py`.

This is the honest hard part of "human-like, not turn-based." Natural next steps:
a learned classifier trained on labeled turn boundaries, richer prosodic features
(speech rate, pause structure), and true barge-in so it can be interrupted.

## Why the reply is streamed end-to-end

`respond.py` doesn't wait for Claude's full reply before saying anything — it
streams raw text deltas as they're generated, and `main.py` feeds them
straight into `tts.speak_stream()`. This matters most on long, detailed
answers: without it, the bot sits in silence for the entire generation time
before saying a single word; with it, time-to-first-audio is roughly the time
to generate the first few words, regardless of how long the full answer ends
up being.

`tts.py` feeds those same raw deltas into ElevenLabs' websocket
`stream-input` endpoint rather than batching them into sentences first. An
earlier version called ElevenLabs once per sentence, which caused audible
glitches at sentence boundaries — each call started a fresh synthesis
context, so ElevenLabs couldn't carry prosody/pacing across the cut. The
websocket endpoint keeps one continuous synthesis context for the whole
utterance and streams PCM audio back as it's generated, which is the native
fit for token-by-token LLM output. There's no fallback engine — if
`ELEVENLABS_API_KEY` is unset or a call fails, `tts.py` raises `TTSError`
rather than silently degrading to a different voice/engine.

## The echo problem (important for an external speaker + mic)

Right now the bot simply **mutes its microphone while it talks** (the `speaking`
flag in `audio.py`). With **headphones** this is fine. With an **external
speaker**, the mic hears the bot's own voice and, worse, can't hear a human who
talks over it (no barge-in).

The real fix is **Acoustic Echo Cancellation (AEC)**, which is required
infrastructure for any speaker+mic setup — not optional:

- **`webrtc-audio-processing`** — the same AEC/noise-suppression/VAD stack used
  in Chrome. Cross-platform, mature, designed for exactly this. Recommended.
- **macOS Voice Processing I/O** — Core Audio's built-in AEC, reachable via
  `AVAudioEngine` (`PyObjC`).

Start with headphones to make progress, then add AEC before going hands-free.

## Notes

- TTS is ElevenLabs-only and works the same on Linux and macOS — audio
  playback goes through `sounddevice`, not a platform-specific command.
- The Claude reply uses `claude-sonnet-4-6` for faster time-to-first-token
  (lower latency to first speech); change `MODEL` in `config.py` to
  `claude-opus-4-8` if you'd rather trade latency for richer replies.
