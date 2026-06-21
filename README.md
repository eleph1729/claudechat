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
| `panelbot/decide.py` | "Should I speak now?" rules | Learned classifier or fast LLM speak/wait/yield |
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

## How it decides to speak (v1)

Two triggers, both in `decide.py`:

- **Addressed** — someone says "Claude" (the `BOT_NAME`). Answers promptly, even
  during cooldown.
- **Lull** — the room is quiet for `SILENCE_TO_SPEAK` seconds and there's
  something on the table. The bot volunteers a brief contribution.

After speaking it stays quiet for `COOLDOWN_AFTER_SPEAKING` seconds so it doesn't
monologue. Everything else → silence. Tune all of this in `config.py`.

This is intentionally simple so you can watch it and feel where it's wrong. The
honest hard part of "human-like, not turn-based" lives here — the natural next
step is to feed timing + prosody (pitch, pause length) into a fast classifier or
a Haiku-speed LLM call that outputs speak / wait / yield.

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
