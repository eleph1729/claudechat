"""Text-to-speech via ElevenLabs, with macOS `say` as a no-key fallback.

`speak()` is called once per sentence (see `respond.py`'s streaming reply), so
a long answer starts being spoken on its first sentence rather than waiting
for the whole thing to be generated. ElevenLabs' `convert_as_stream` streams
audio back as it's synthesized, so even a single sentence starts playing
before the rest of it has finished generating server-side.

Each call blocks until that sentence has finished playing, so the caller can
hold the mic's `speaking` flag for exactly the right duration.
"""

import subprocess

import config

_client = None


def _elevenlabs_client():
    global _client
    if _client is None:
        from elevenlabs.client import ElevenLabs
        _client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _client


def _speak_say(text: str):
    cmd = ["say", "-r", str(config.TTS_RATE_WPM)]
    if config.TTS_VOICE:
        cmd += ["-v", config.TTS_VOICE]
    cmd.append(text)
    subprocess.run(cmd, check=False)


def _speak_elevenlabs(text: str):
    from elevenlabs import stream as play_stream
    audio = _elevenlabs_client().text_to_speech.convert_as_stream(
        text=text,
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
    )
    play_stream(audio)


def speak(text: str):
    if not text:
        return
    if config.ELEVENLABS_API_KEY:
        try:
            _speak_elevenlabs(text)
            return
        except Exception as e:
            print(f"  [tts] ElevenLabs failed ({e}); falling back to `say`.")
    _speak_say(text)
