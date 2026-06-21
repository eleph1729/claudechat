# Panel Bot

A chatbot that listens to a live panel discussion and decides *when* — and
whether — to speak, rather than just taking strict turns. Built to run on a
MacBook Pro with audio in/out from the Mac.

## Pipeline

```
mic ──VAD──> utterances ──> Whisper STT ──> transcript ──> turn-taking ──> Claude ──> macOS `say`
```

Each stage is its own small module so you can improve them independently:

| File | Job | Grow it into |
| --- | --- | --- |
| `panelbot/audio.py` | Mic capture + voice-activity detection | Real acoustic echo cancellation (see below) |
| `panelbot/stt.py` | Local speech-to-text (faster-whisper) | Streaming/partial transcripts |
| `panelbot/prosody.py` | Pitch/energy cues from each utterance | Richer features (speech rate, pause structure) |
| `panelbot/decide.py` | "Should I speak now?" — rules + fast LLM speak/wait/yield | Learned classifier; interruption/barge-in |
| `panelbot/respond.py` | Generate the spoken reply (Claude) | Per-speaker memory, interruption awareness |
| `panelbot/tts.py` | Speak via macOS `say` | Streaming TTS (ElevenLabs/Piper) with barge-in |
| `panelbot/main.py` | The loop tying it together | |
| `config.py` | All the knobs (thresholds, model, voice) | |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # for the Claude reply step
python -m panelbot.main
```

First run downloads the Whisper model (`base.en`, ~150 MB). `say` and the
microphone are built into macOS; grant terminal mic permission when prompted.

## How it decides to speak

`decide.py` is a hybrid of cheap rules and a fast LLM judgment:

- **Addressed** (rule) — someone says "Claude" (the `BOT_NAME`). Answers
  promptly, even during cooldown.
- **Cooldown** (rule) — for `COOLDOWN_AFTER_SPEAKING` seconds after it talks, it
  stays quiet so it doesn't monologue.
- **Volunteer** (AI) — on a fresh remark, or the moment a real lull opens up, a
  small fast model (`DECIDE_MODEL`, Haiku by default) weighs the transcript plus
  **prosody** and **timing** and returns `speak` / `wait` / `yield`. Only `speak`
  takes the floor.

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

- This was developed on Linux but targets macOS (`say` is Mac-only). On Linux,
  swap `tts.py` for `espeak`/`piper` to test the loop.
- The Claude reply uses `claude-opus-4-8`; change `MODEL` in `config.py`. For
  lower latency you might try a faster model for the reply step.
