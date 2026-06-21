"""Turn-taking decision: should the bot speak right now?

This is the heart of "not just turn-taking". For v1 it is deliberately simple
and rule-based so you can watch it behave and tune thresholds. Two triggers:

  1. ADDRESSED  — someone said the bot's name, or asked a direct question right
                  after it. The bot answers promptly.
  2. LULL       — the room has gone quiet for SILENCE_TO_SPEAK seconds and there
                  is something on the table worth responding to. The bot may
                  volunteer.

Everything else => stay silent. Later this module is the natural place to swap
in a learned classifier or a fast LLM "speak/wait/yield" call fed with prosody
and timing features.
"""

import time

import config


class Decision:
    SPEAK_ADDRESSED = "addressed"
    SPEAK_LULL = "lull"
    STAY_SILENT = "silent"


class TurnTaker:
    def __init__(self):
        self._last_spoke_at = 0.0

    def note_bot_spoke(self):
        self._last_spoke_at = time.monotonic()

    def _in_cooldown(self) -> bool:
        return (time.monotonic() - self._last_spoke_at) < config.COOLDOWN_AFTER_SPEAKING

    def _addressed(self, text: str) -> bool:
        return config.BOT_NAME.lower() in text.lower()

    def decide(self, latest_text: str, seconds_since_voice: float) -> str:
        """Return one of Decision.* given the latest utterance and silence length."""
        # Being addressed by name overrides cooldown — answer when called on.
        if latest_text and self._addressed(latest_text):
            return Decision.SPEAK_ADDRESSED

        if self._in_cooldown():
            return Decision.STAY_SILENT

        # Volunteer into a genuine lull, but only if there's content to react to.
        if latest_text and seconds_since_voice >= config.SILENCE_TO_SPEAK:
            return Decision.SPEAK_LULL

        return Decision.STAY_SILENT
