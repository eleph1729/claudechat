"""Text-to-speech via the macOS `say` command.

Dead simple and dependency-free on a Mac. It blocks until speech finishes so the
caller can hold the mic's `speaking` flag for exactly that long. Swap this module
for a streaming TTS (e.g. ElevenLabs, Piper) later for lower latency and the
ability to be interrupted mid-sentence.
"""

import subprocess

import config


def speak(text: str):
    if not text:
        return
    cmd = ["say", "-r", str(config.TTS_RATE_WPM)]
    if config.TTS_VOICE:
        cmd += ["-v", config.TTS_VOICE]
    cmd.append(text)
    subprocess.run(cmd, check=False)
