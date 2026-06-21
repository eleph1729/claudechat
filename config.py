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
SILENCE_TO_SPEAK = 2.5
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
MODEL = "claude-opus-4-8"
MAX_TOKENS = 400             # panel replies should be short and spoken-friendly

# --- TTS --------------------------------------------------------------------
# ElevenLabs is used when ELEVENLABS_API_KEY is set (export it in your shell);
# otherwise we fall back to macOS `say`. ElevenLabs streams audio back as it's
# synthesized, so pairing it with the sentence-streamed reply (respond.py)
# gets audio out the door as fast as possible, even on long answers.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel"
ELEVENLABS_MODEL = "eleven_turbo_v2_5"   # lowest-latency ElevenLabs model

# macOS `say` voice (fallback only). Run `say -v '?'` to list. None = system default.
TTS_VOICE = None
TTS_RATE_WPM = 180
