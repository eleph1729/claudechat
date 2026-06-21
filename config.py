"""Central configuration for the panel bot.

Tweak these while iterating. Times are in seconds unless noted.
"""

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
# After the bot speaks, stay quiet at least this long to avoid monologuing.
COOLDOWN_AFTER_SPEAKING = 4.0
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
# macOS `say` voice. Run `say -v '?'` to list. None = system default.
TTS_VOICE = None
TTS_RATE_WPM = 180
