"""Generate a spoken panel response with Claude.

Keeps a running transcript of the conversation and asks Claude for a short,
conversational reply suitable for being spoken aloud on a panel. The transcript
is sent as a single user turn each time; the system prompt carries the persona
and the "keep it brief / spoken" constraints.

The reply is streamed as raw text deltas — no sentence-splitting here. TTS
(see `tts.py`) feeds those deltas straight into ElevenLabs' websocket endpoint,
which keeps one synthesis context for the whole utterance; pre-chunking into
sentences ourselves was causing audible glitches/resets at each chunk boundary.
"""

import anthropic

import config

SYSTEM = f"""You are {config.BOT_NAME}, a participant in {config.PANEL_TOPIC} \
alongside one or more humans. You hear a live transcript of the room.

Speak like a thoughtful panelist, not an assistant:
- By default, be brief: one or two sentences, occasionally three. This will be \
spoken aloud.
- Be conversational and natural. No lists, no markdown, no stage directions.
- Add a point, build on what was said, or answer the question — don't summarize \
the discussion back to people.
- If you were addressed by name, respond to that directly.
- It's fine to be light or to disagree. Don't hedge excessively.

If anyone has given you an explicit instruction about how you should respond \
(e.g. "give me a one word answer", "answer in French", "keep it under 10 \
seconds"), follow it even if it overrides the defaults above — treat it as a \
standing instruction until they say otherwise.

You only speak when handed the floor, so make it count, then stop."""


class Responder:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.transcript: list[str] = []

    def add_heard(self, text: str):
        """Record something a human said into the running transcript."""
        self.transcript.append(f"Speaker: {text}")

    def add_self(self, text: str):
        self.transcript.append(f"{config.BOT_NAME}: {text}")

    def _recent(self, n: int = 30) -> str:
        return "\n".join(self.transcript[-n:])

    def transcript_text(self, n: int = 30) -> str:
        """Recent transcript as plain text (for the turn-taking decision)."""
        return self._recent(n)

    def reply_stream(self, reason: str = ""):
        """Stream the next spoken line, yielding raw text deltas as they arrive.

        `reason` is the turn-taking model's free-text reason for speaking now
        (e.g. "addressed by name", "falling intonation + real lull") — passed
        through as a hint, not a fixed category.
        """
        nudge = f"You judged this a good moment to speak ({reason})." if reason \
            else "Respond if you have something worth saying."

        prompt = f"Here is the recent conversation:\n\n{self._recent()}\n\n{nudge}"

        parts: list[str] = []
        with self.client.messages.stream(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for delta in stream.text_stream:
                parts.append(delta)
                yield delta

        full_text = "".join(parts).strip()
        if full_text:
            self.add_self(full_text)
