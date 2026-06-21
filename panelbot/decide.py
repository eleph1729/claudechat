"""Turn-taking decision: should the bot speak right now?

There are no hardcoded behavioral rules here (no "always answer if addressed",
no fixed cooldown timer). Instead, a small fast model looks at the transcript,
the latest utterance's *prosody* (rising vs falling intonation, trailing
energy), how long the room has been silent, and how recently the bot itself
spoke, and decides speak / wait / yield. This lets it generally understand and
honor whatever instructions the humans actually give it in conversation
("Claude, don't speak again until I say the word potato") rather than us
trying to special-case every such instruction in code.

The "trigger" logic below (only deliberate on a fresh remark or a freshly
opened lull) is purely a cost/latency optimization — it controls how often we
bother calling the model, not what it's allowed to decide.

If the AI step is disabled or errors, we fall back to a bare silence rule so
the loop keeps working offline.
"""

import time
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

import config
from panelbot.prosody import Prosody


class Decision:
    SPEAK = "speak"
    STAY_SILENT = "silent"


DECIDE_SYSTEM = f"""You are the turn-taking controller for {config.BOT_NAME}, a \
participant in a live spoken panel. You do NOT write replies — you only decide \
whether this is a good moment for {config.BOT_NAME} to start talking.

There are no fixed rules here (no automatic "always answer if addressed", no \
fixed cooldown). Use your judgment, the same way an attentive human panelist \
would. In particular:

- If anyone has given {config.BOT_NAME} an explicit instruction about when it \
should or shouldn't speak (e.g. "don't say anything until I say the word \
potato", "wait until I finish my point", "only talk if asked a direct \
question"), follow it — it overrides your normal judgment until they say \
otherwise or the context makes clear it no longer applies.
- Avoid monologuing: if {config.BOT_NAME} just spoke very recently, lean \
toward letting others talk unless directly addressed or there's a good reason.
- Falling intonation + trailing-off energy + a real silence favors "speak"; \
rising intonation or steady energy favors "wait"/"yield". Being addressed by \
name is a strong (not absolute) reason to speak.
- Be eager to fill a natural gap. A comfortable pause after a finished thought \
(a few seconds of silence) is a genuine opening — lean toward "speak" and \
contribute rather than letting the conversation stall.
- BUT a very long silence (roughly {config.LONG_SILENCE:.0f}s or more) usually \
means the panel is busy with something else — thinking, reading, working \
off-mic — not waiting for you. Don't break a long silence out of nowhere; \
prefer "wait" and let them resume in their own time.

You are given the recent transcript, the latest thing said, prosodic cues \
about how it was said, how long the room has been silent, and how recently \
{config.BOT_NAME} last spoke. Output one action:

- "speak": now is a natural opening.
- "wait": stay quiet and keep listening — the speaker is mid-thought, just \
took a breath, or the moment isn't yours yet.
- "yield": actively hold back — someone else has the floor, is about to \
continue, or has instructed {config.BOT_NAME} to stay quiet.

Be a good listener, not an interrupter. When unsure, prefer "wait" over \
"speak". Keep "reason" to a few words."""


class _AIDecision(BaseModel):
    action: Literal["speak", "wait", "yield"]
    reason: str


class SpeakClassifier:
    """Wraps the fast speak/wait/yield model call (structured output)."""

    def __init__(self):
        self.client = anthropic.Anthropic()

    def decide(self, transcript: str, latest: str, prosody: Optional[Prosody],
               seconds_since_voice: float, seconds_since_bot_spoke: Optional[float]) -> _AIDecision:
        prosody_line = prosody.describe() if prosody else "unknown"
        last_spoke_line = (f"{seconds_since_bot_spoke:.1f}s ago" if seconds_since_bot_spoke is not None
                            else "hasn't spoken yet this session")
        prompt = (
            f"Recent transcript:\n{transcript or '(nothing yet)'}\n\n"
            f"Latest thing said: \"{latest}\"\n"
            f"How it was said: {prosody_line}\n"
            f"Room has been silent for: {seconds_since_voice:.1f}s\n"
            f"{config.BOT_NAME} last spoke: {last_spoke_line}\n\n"
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
        self._last_spoke_at: Optional[float] = None
        # When we last deliberated about volunteering into ongoing silence —
        # throttles lull re-checks to LULL_RECHECK_INTERVAL instead of every tick.
        self._last_lull_decision_at = 0.0
        self.last_note = ""        # human-readable trace of the last decision
        self.classifier: Optional[SpeakClassifier] = None
        if config.USE_AI_DECISION:
            try:
                self.classifier = SpeakClassifier()
            except Exception as e:
                print(f"  [decide] AI decision unavailable ({e}); using rules.")

    def note_bot_spoke(self):
        self._last_spoke_at = time.monotonic()

    def _seconds_since_bot_spoke(self) -> Optional[float]:
        if self._last_spoke_at is None:
            return None
        return time.monotonic() - self._last_spoke_at

    def decide(self, latest_text: str, prosody: Optional[Prosody],
               seconds_since_voice: float, transcript: str = "") -> str:
        """Return one of Decision.* for the current moment."""
        now = time.monotonic()
        new_utterance = bool(latest_text)
        if new_utterance:
            # Fresh content re-arms volunteering immediately.
            self._last_lull_decision_at = 0.0

        # Volunteer into a gap only while a human was the most recent speaker —
        # if the bot itself spoke last, a following silence isn't an opening,
        # it's just nobody having replied yet (don't monologue).
        since_bot = self._seconds_since_bot_spoke()
        human_spoke_last = since_bot is None or since_bot > seconds_since_voice

        # Re-deliberate through the gap, but only within a bounded window:
        # from SILENCE_TO_SPEAK (eager start) up to LONG_SILENCE (past which a
        # long pause means the panel is busy — wait for them to resume).
        in_lull_window = (config.SILENCE_TO_SPEAK <= seconds_since_voice
                          <= config.LONG_SILENCE)
        lull_recheck = (in_lull_window and human_spoke_last and
                        (now - self._last_lull_decision_at) >= config.LULL_RECHECK_INTERVAL)

        # Deliberate on a fresh remark or a (throttled) lull re-check — not on
        # every 0.1s tick. This is a cost/latency gate, not a rule about whether
        # the bot is "allowed" to speak.
        trigger = new_utterance or lull_recheck
        if not trigger or not transcript:
            return Decision.STAY_SILENT
        if not new_utterance:
            self._last_lull_decision_at = now

        if self.classifier:
            try:
                ai = self.classifier.decide(transcript, latest_text, prosody,
                                            seconds_since_voice, self._seconds_since_bot_spoke())
                self.last_note = f"{ai.action}: {ai.reason}"
                print(f"  [decide] {self.last_note}")
                if ai.action == "speak":
                    return Decision.SPEAK
                return Decision.STAY_SILENT
            except Exception as e:
                # Don't let a transient API hiccup kill the live loop.
                print(f"  [decide] AI call failed ({e}); falling back to rule.")

        # Fallback rule (offline / API down): volunteer into a genuine lull,
        # within the same bounded window the AI path uses.
        if in_lull_window and human_spoke_last:
            self.last_note = "lull (rule)"
            return Decision.SPEAK
        return Decision.STAY_SILENT
