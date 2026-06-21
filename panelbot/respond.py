"""Generate a spoken panel response with Claude.

Keeps a running transcript of the conversation and asks Claude for a short,
conversational reply suitable for being spoken aloud on a panel. The transcript
is sent as a single user turn each time; the system prompt carries the persona
and the "keep it brief / spoken" constraints.
"""

import anthropic

import config

SYSTEM = f"""You are {config.BOT_NAME}, a participant in {config.PANEL_TOPIC} \
alongside one or more humans. You hear a live transcript of the room.

Speak like a thoughtful panelist, not an assistant:
- Be brief. One or two sentences, occasionally three. This will be spoken aloud.
- Be conversational and natural. No lists, no markdown, no stage directions.
- Add a point, build on what was said, or answer the question — don't summarize \
the discussion back to people.
- If you were addressed by name, respond to that directly.
- It's fine to be light or to disagree. Don't hedge excessively.

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

    def reply(self, reason: str) -> str:
        """Ask Claude for the next spoken line. `reason` is a Decision.* value."""
        nudge = {
            "addressed": "You were just addressed. Respond directly.",
            "volunteer": "You judged this a good moment to jump in. Make a brief, "
                         "useful contribution — don't summarize what was said.",
            "lull": "The room has gone quiet. Offer a brief, useful contribution.",
        }.get(reason, "Respond if you have something worth saying.")

        prompt = f"Here is the recent conversation:\n\n{self._recent()}\n\n{nudge}"

        message = self.client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in message.content if b.type == "text"), "").strip()
        if text:
            self.add_self(text)
        return text
