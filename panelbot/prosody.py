"""Lightweight prosodic feature extraction from raw PCM.

Humans take turns on *how* something is said, not just the words. A rising
pitch at the end of an utterance invites a response; a falling pitch signals a
finished, settled thought; energy that trails off hints the speaker is yielding
the floor. We compute a few cheap, robust cues here and hand them to the
decision step (`decide.py`) so it can reason about timing the way a person does.

Everything is numpy-only (no extra dependencies) and assumes 16 kHz mono int16,
the format `audio.py` already produces. None of this is research-grade pitch
tracking — it just needs to be good enough to tell "question?" from "...done."
"""

from dataclasses import dataclass

import numpy as np

import config


@dataclass
class Prosody:
    """A compact summary of how an utterance was spoken."""

    duration_s: float
    energy: float                 # overall loudness (RMS, 0-1ish)
    mean_pitch_hz: float          # 0.0 if no voiced pitch was found
    pitch_slope_hz_s: float       # final intonation trend; + rising, - falling
    trailing_energy_ratio: float  # last 200ms energy / overall; <1 = trailing off

    def describe(self) -> str:
        """Render a short natural-language summary for the decision prompt."""
        if self.mean_pitch_hz == 0:
            pitch = "no clear pitch (quiet or unvoiced)"
        elif self.pitch_slope_hz_s > config.PITCH_SLOPE_THRESHOLD:
            pitch = (f"rising intonation (+{self.pitch_slope_hz_s:.0f} Hz/s) — "
                     "sounds like a question or an invitation to respond")
        elif self.pitch_slope_hz_s < -config.PITCH_SLOPE_THRESHOLD:
            pitch = (f"falling intonation ({self.pitch_slope_hz_s:.0f} Hz/s) — "
                     "sounds like a finished, settled thought")
        else:
            pitch = "flat intonation — possibly mid-thought"

        if self.trailing_energy_ratio < 0.5:
            tail = "trailing off in volume (winding down or yielding the floor)"
        elif self.trailing_energy_ratio > 1.2:
            tail = "ending strongly (may keep going)"
        else:
            tail = "steady volume to the end"

        return f"~{self.duration_s:.1f}s; {pitch}; {tail}"


def _f0(window: np.ndarray, sr: int) -> float:
    """Estimate fundamental frequency of one short window via autocorrelation.

    Returns 0.0 when the window is too quiet or not clearly periodic (unvoiced).
    """
    w = window - window.mean()
    if np.sqrt((w ** 2).mean()) < 1e-3:
        return 0.0

    corr = np.correlate(w, w, mode="full")[len(w) - 1:]
    if corr[0] <= 0:
        return 0.0

    lo = int(sr / config.PITCH_MAX_HZ)   # shortest lag = highest pitch
    hi = int(sr / config.PITCH_MIN_HZ)   # longest lag = lowest pitch
    if hi >= len(corr) or lo < 1:
        return 0.0

    seg = corr[lo:hi]
    if len(seg) == 0:
        return 0.0
    peak = int(np.argmax(seg)) + lo
    # Weak periodicity relative to zero-lag energy => treat as unvoiced.
    if corr[peak] / corr[0] < 0.3:
        return 0.0
    return sr / peak


def extract(pcm: bytes) -> Prosody:
    """Compute a Prosody summary from raw int16 PCM bytes."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    sr = config.SAMPLE_RATE
    if len(samples) == 0:
        return Prosody(0.0, 0.0, 0.0, 0.0, 1.0)

    duration = len(samples) / sr
    energy = float(np.sqrt((samples ** 2).mean()))

    # Track pitch across the ending window, where turn-final cues concentrate.
    end = samples[-int(config.ENDING_WINDOW * sr):] if duration > config.ENDING_WINDOW else samples
    win = int(0.03 * sr)  # 30 ms analysis windows
    times, freqs = [], []
    for start in range(0, max(0, len(end) - win), win):
        f = _f0(end[start:start + win], sr)
        if f > 0:
            times.append(start / sr)
            freqs.append(f)

    if len(freqs) >= 2:
        slope = float(np.polyfit(times, freqs, 1)[0])  # Hz per second
        mean_pitch = float(np.mean(freqs))
    elif freqs:
        slope, mean_pitch = 0.0, float(freqs[0])
    else:
        slope, mean_pitch = 0.0, 0.0

    tail = samples[-int(0.2 * sr):] if duration > 0.2 else samples
    tail_energy = float(np.sqrt((tail ** 2).mean()))
    trailing_ratio = (tail_energy / energy) if energy > 0 else 1.0

    return Prosody(duration, energy, mean_pitch, slope, trailing_ratio)
