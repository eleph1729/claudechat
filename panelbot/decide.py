"""Turn-taking decision: should the bot speak right now?

This is the heart of "not just turn-taking". It is a hybrid of cheap rules and
a fast LLM judgment:

  * Instant rules handle the unambiguous cases with zero latency/cost:
      - ADDRESSED  — someone said the bot's name -> answer promptly.
      - COOLDOWN   — we just spoke -> stay quiet so we don't monologue.

  * For the genuinely hard call — "there's a pause / a new remark, should I jump
    in or hold back?" — we ask a small, fast model to weigh the transcript plus
    *prosody* (rising vs falling intonation, trailing energy) and *timing*
    (how long the silence is), and return speak / wait / yield. This is the
    natural place the README points to: feeding timing + prosody into a
    Haiku-speed speak/wait/yield call.

If the AI step is disabled or errors, we fall back to the original silence rule
so the loop keeps working offline.
"""

import time
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

import config
from panelbot.prosody import Prosody


class Decision:
    SPEAK_ADDRESSED = "addressed"
    SPEAK_VOLUNTEER = "volunteer"
    STAY_SILENT = "silent"


DECIDE_SYSTEM = f"""You are the turn-taking controller for {config.BOT_NAME}, a \
participant in a live spoken panel. You do NOT write replies — you only decide \
whether this is a good moment for {config.BOT_NAME} to start talking.

You are given the recent transcript, the latest thing said, prosodic cues about \
how it was said, and how long the room has been silent. Output one action:

- "speak": now is a natural opening — a real lull after a finished thought, or a \
question/point clearly left open for someone to take up.
- "wait": stay quiet and keep listening — the speaker is mid-thought, just took a \
breath, or the moment isn't yours yet.
- "yield": actively hold back — someone else has the floor or is about to \
continue; jumping in would talk over them.

Be a good listener, not an interrupter. When unsure, prefer "wait" over "speak". \
Falling intonation + trailing-off energy + a real silence favors "speak"; rising \
intonation or steady energy favors "wait"/"yield". Keep "reason" to a few words."""


class _AIDecision(BaseModel):
    action: Literal["speak", "wait", "yield"]
    reason: str


class SpeakClassifier:
    """Wraps the fast speak/wait/yield model call (structured output)."""

    def __init__(self):
        self.client = anthropic.Anthropic()

    def decide(self, transcript: str, latest: str, prosody: Optional[Prosody],
               seconds_since_voice: float) -> _AIDecision:
        prosody_line = prosody.describe() if prosody else "unknown"
        prompt = (
            f"Recent transcript:\n{transcript or '(nothing yet)'}\n\n"
            f"Latest thing said: \"{latest}\"\n"
            f"How it was said: {prosody_line}\n"
            f"Room has been silent for: {seconds_since_voice:.1f}s\n\n"
            f"Should {config.BOT_NAME} speak now?"
        )
        message = self.client.messages.parse(
            model=config.DECIDE_MODEL,
            max_tokens=config.DECIDE_MAX_TOKENS,
            system=DECIDE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_AIDecision,
        )
        return message.parsed_output


class TurnTaker:
    def __init__(self):
        self._last_spoke_at = 0.0
        # Re-armed by each new utterance, consumed once per lull, so we make at
        # most one volunteer decision per thing said (keeps the loop cheap).
        self._lull_pending = False
        self.last_note = ""        # human-readable trace of the last decision
        self.classifier: Optional[SpeakClassifier] = None
        if config.USE_AI_DECISION:
            try:
                self.classifier = SpeakClassifier()
            except Exception as e:
                print(f"  [decide] AI decision unavailable ({e}); using rules.")

    def note_bot_spoke(self):
        self._last_spoke_at = time.monotonic()

    def _in_cooldown(self) -> bool:
        return (time.monotonic() - self._last_spoke_at) < config.COOLDOWN_AFTER_SPEAKING

    def _addressed(self, text: str) -> bool:
        return config.BOT_NAME.lower() in text.lower()

    def decide(self, latest_text: str, prosody: Optional[Prosody],
               seconds_since_voice: float, transcript: str = "") -> str:
        """Return one of Decision.* for the current moment."""
        # Being addressed by name overrides everything — answer when called on.
        if latest_text and self._addressed(latest_text):
            self.last_note = "addressed by name"
            return Decision.SPEAK_ADDRESSED

        if self._in_cooldown():
            return Decision.STAY_SILENT

        new_utterance = bool(latest_text)
        if new_utterance:
            self._lull_pending = True
        lull = seconds_since_voice >= config.SILENCE_TO_SPEAK

        # Only deliberate on a fresh remark or the moment a real lull opens up —
        # not on every 0.1s tick.
        trigger = new_utterance or (lull and self._lull_pending)
        if not trigger or not transcript:
            return Decision.STAY_SILENT
        self._lull_pending = False

        if self.classifier:
            try:
                ai = self.classifier.decide(transcript, latest_text, prosody,
                                            seconds_since_voice)
                self.last_note = f"{ai.action}: {ai.reason}"
                print(f"  [decide] {self.last_note}")
                if ai.action == "speak":
                    return Decision.SPEAK_VOLUNTEER
                return Decision.STAY_SILENT
            except Exception as e:
                # Don't let a transient API hiccup kill the live loop.
                print(f"  [decide] AI call failed ({e}); falling back to rule.")

        # Fallback rule: volunteer into a genuine lull with content on the table.
        if lull and latest_text:
            self.last_note = "lull (rule)"
            return Decision.SPEAK_VOLUNTEER
        return Decision.STAY_SILENT
