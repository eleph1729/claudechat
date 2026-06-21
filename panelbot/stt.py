"""Speech-to-text using faster-whisper (local, runs well on Apple Silicon)."""

from faster_whisper import WhisperModel

import config
from panelbot.audio import pcm_to_float32


class Transcriber:
    def __init__(self):
        self.model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )

    def transcribe(self, pcm: bytes) -> str:
        audio = pcm_to_float32(pcm)
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1)
        return " ".join(seg.text for seg in segments).strip()
