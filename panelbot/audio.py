"""Microphone capture with voice-activity detection.

Captures mono 16 kHz audio from the default input device and groups it into
"utterances" — runs of speech bracketed by silence. Each completed utterance is
pushed onto a queue as raw int16 PCM bytes for the STT layer to transcribe.

A `speaking` flag lets the caller pause utterance emission while the bot's own
voice is playing, which is a crude form of echo suppression. It is NOT real
acoustic echo cancellation (AEC) — with an external speaker + mic you will
eventually want webrtc-audio-processing or macOS voice-processing I/O. See
README for notes. For a first run with headphones it is good enough.
"""

import collections
import queue
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

import config


class Microphone:
    def __init__(self):
        self.vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)
        self.frame_len = int(config.SAMPLE_RATE * config.FRAME_MS / 1000)
        self.utterances: "queue.Queue[bytes]" = queue.Queue()
        # When True, drop incoming audio (the bot is talking — avoid self-hearing).
        self.speaking = threading.Event()
        self._stop = threading.Event()
        # Tracks the most recent moment speech was heard, for silence timing.
        self._last_voice_time = threading.Event()
        self.seconds_since_voice = 0.0
        self._stream = None

    def _is_speech(self, frame: bytes) -> bool:
        return self.vad.is_speech(frame, config.SAMPLE_RATE)

    def start(self):
        """Begin capture in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # Ring buffer of recent frames so we keep a little pre-speech padding.
        ring = collections.deque(maxlen=10)
        voiced: list[bytes] = []
        triggered = False
        silence_frames = 0
        frames_per_sec = 1000 / config.FRAME_MS

        with sd.RawInputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=self.frame_len,
            dtype="int16",
            channels=config.CHANNELS,
        ) as stream:
            self._stream = stream
            while not self._stop.is_set():
                data, _ = stream.read(self.frame_len)
                frame = bytes(data)

                # While the bot is speaking, throw audio away and reset state.
                if self.speaking.is_set():
                    triggered, voiced, silence_frames = False, [], 0
                    self.seconds_since_voice = 0.0
                    continue

                is_speech = self._is_speech(frame)

                if not triggered:
                    ring.append(frame)
                    if is_speech:
                        triggered = True
                        voiced.extend(ring)
                        ring.clear()
                        silence_frames = 0
                    else:
                        self.seconds_since_voice += 1 / frames_per_sec
                else:
                    voiced.append(frame)
                    if is_speech:
                        silence_frames = 0
                    else:
                        silence_frames += 1
                        # End the utterance after ~0.5 s of trailing silence.
                        if silence_frames > frames_per_sec * 0.5:
                            self._emit(voiced)
                            triggered, voiced, silence_frames = False, [], 0
                            self.seconds_since_voice = 0.0

    def _emit(self, frames: list[bytes]):
        pcm = b"".join(frames)
        duration = len(pcm) / 2 / config.SAMPLE_RATE  # int16 -> 2 bytes/sample
        if duration >= config.MIN_UTTERANCE:
            self.utterances.put(pcm)


def pcm_to_float32(pcm: bytes) -> np.ndarray:
    """Convert int16 PCM bytes to the float32 array faster-whisper expects."""
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return audio / 32768.0
