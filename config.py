"""Central configuration for the panel bot.

Tweak these while iterating. Times are in seconds unless noted.
"""

import os

# --- Identity ---------------------------------------------------------------
BOT_NAME = "Claude"          # what humans call the bot to address it directly
PANEL_TOPIC = "an open panel discussion"

# --- Audio ------------------------------------------------------------------
SAMPLE_RATE = 16000          # webrtcvad + whisper both like 16 kHz mono
FRAME_MS = 30                # webrtcvad accepts 10/20/30 ms frames
CHANNELS = 1

# --- Turn-taking ------------------------------------------------------------
# How long the room must be silent before the bot considers volunteering.
# Lower = more eager to jump into a natural gap.
SILENCE_TO_SPEAK = 1.5
# While silence continues (and a human spoke last), re-evaluate whether to
# jump in this often. Lets the bot take a gap that opens up a moment later,
# not only at the instant silence crosses SILENCE_TO_SPEAK.
LULL_RECHECK_INTERVAL = 2.5
# Past this much continuous silence, stop volunteering into the gap — a very
# long pause usually means the panel is busy with something off-mic, so wait
# for them to resume (a new utterance re-arms volunteering) rather than break
# a long silence out of nowhere.
LONG_SILENCE = 12.0
# Minimum speech (seconds) we need before a transcript is worth acting on.
MIN_UTTERANCE = 0.4
# VAD aggressiveness 0-3 (3 = most aggressive at filtering non-speech).
VAD_AGGRESSIVENESS = 2

# --- AI turn-taking (decide.py) ---------------------------------------------
# When True, a fast model weighs timing + prosody + transcript to choose
# speak/wait/yield. When False (or on any API error) we fall back to the
# simple silence rule. This call runs in the live loop, so it uses a small,
# low-latency model; the spoken reply still uses MODEL below.
USE_AI_DECISION = True
DECIDE_MODEL = "claude-haiku-4-5"   # fast = can react in time to take a turn
DECIDE_MAX_TOKENS = 150

# --- Prosody (prosody.py) ---------------------------------------------------
# Pitch-tracking search range (human speech f0 lives roughly here).
PITCH_MIN_HZ = 75
PITCH_MAX_HZ = 400
# Seconds at the END of an utterance used to judge intonation/trailing energy.
# The end of a turn carries most of the "am I done?" signal.
ENDING_WINDOW = 0.6
# How steep (Hz/sec) the final pitch trend must be to call it rising/falling.
PITCH_SLOPE_THRESHOLD = 15.0

# --- STT --------------------------------------------------------------------
WHISPER_MODEL = "base.en"    # base.en is a good latency/quality start on M-series
WHISPER_DEVICE = "auto"      # "auto" | "cpu" | "cuda"
WHISPER_COMPUTE = "int8"     # int8 is fast on CPU; try "float16" on GPU

# --- LLM --------------------------------------------------------------------
MODEL = "claude-sonnet-4-6"  # faster time-to-first-token than Opus for low-latency speech
MAX_TOKENS = 400             # panel replies should be short and spoken-friendly

# --- TTS --------------------------------------------------------------------
# ElevenLabs only — no fallback engine. Export ELEVENLABS_API_KEY before
# running; the bot raises immediately at startup if it's unset.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel"
ELEVENLABS_MODEL = "eleven_turbo_v2_5"   # lowest-latency ElevenLabs model
# How many characters ElevenLabs buffers before synthesizing each audio chunk.
# Its default is [120, 160, 250, 290] — it waits for ~120 chars before the
# FIRST audio comes back, which dominates time-to-first-speech. A small first
# value flushes audio much sooner; the later (larger) values keep prosody
# smooth once we're rolling. Min allowed is 50.
ELEVENLABS_CHUNK_SCHEDULE = [50, 120, 200, 260]
# Playback sample rate. ElevenLabs returns pcm_<rate>; we play it at the same
# rate. 24000 is an exact 2x divisor of typical 48 kHz Mac hardware, so the
# OS resamples cleanly — 16000 forced an awkward ratio that added crackle.
TTS_SAMPLE_RATE = 24000
# Seconds of audio to buffer before starting playback. A larger lead lets the
# jitter buffer ride over gaps between ElevenLabs chunks without underrunning
# (heard as crackle/clicks). Larger = smoother but slightly later first speech.
TTS_PRIME_SECONDS = 1.0
# Print time-to-first-audio for each reply so latency is visible.
TTS_TIMING = True
