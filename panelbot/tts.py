"""Text-to-speech via ElevenLabs only.

`speak_stream()` is the main entry point: it takes an iterator of raw text
chunks (as they stream out of Claude — see `respond.py`) and feeds them
straight into ElevenLabs' websocket `stream-input` endpoint as they arrive,
playing back the synthesized audio as it streams back. ElevenLabs keeps a
single synthesis context for the whole utterance this way, instead of
resetting context on every call (which is what happens — and causes audible
glitches — if you instead call a non-streaming endpoint once per sentence).

Audio comes back as raw 16-bit PCM (`output_format=pcm_16000`) so it can be
written straight to a `sounddevice` output stream with no decoding step.

There is no fallback TTS engine: if `ELEVENLABS_API_KEY` isn't set, or the
websocket call fails, this raises rather than silently degrading to a
different voice/engine.
"""

import asyncio
import base64
import json
from typing import Iterable

import sounddevice as sd
import websockets

import config


class TTSError(RuntimeError):
    """Raised when ElevenLabs TTS is unavailable or a call to it fails."""


def _require_api_key():
    if not config.ELEVENLABS_API_KEY:
        raise TTSError(
            "ELEVENLABS_API_KEY is not set. This bot requires ElevenLabs for "
            "TTS — export ELEVENLABS_API_KEY before running."
        )


async def _elevenlabs_stream(text_chunks: Iterable[str]):
    uri = (f"wss://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
           f"/stream-input?model_id={config.ELEVENLABS_MODEL}&output_format=pcm_16000")

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _feed():
        # Runs in a worker thread so iterating the (blocking) Claude stream
        # doesn't stall the event loop. Always drains text_chunks fully, even
        # if the websocket side fails, so respond.py still records the full
        # reply in its transcript.
        for chunk in text_chunks:
            if chunk:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    feed_future = loop.run_in_executor(None, _feed)

    try:
        async with websockets.connect(
            uri, additional_headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        ) as ws:
            await ws.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
            }))

            async def sender():
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        await ws.send(json.dumps({"text": ""}))
                        break
                    await ws.send(json.dumps({"text": chunk}))

            async def receiver():
                stream = sd.RawOutputStream(samplerate=16000, channels=1, dtype="int16")
                stream.start()
                try:
                    async for message in ws:
                        data = json.loads(message)
                        audio_b64 = data.get("audio")
                        if audio_b64:
                            stream.write(base64.b64decode(audio_b64))
                        if data.get("isFinal"):
                            break
                finally:
                    stream.stop()
                    stream.close()

            sender_task = asyncio.create_task(sender())
            receiver_task = asyncio.create_task(receiver())
            try:
                await asyncio.gather(sender_task, receiver_task)
            except Exception:
                sender_task.cancel()
                receiver_task.cancel()
                raise
    finally:
        await feed_future


def ensure_configured():
    """Raise TTSError now if ElevenLabs isn't configured, instead of waiting
    for the first reply to fail."""
    _require_api_key()


def speak_stream(text_chunks: Iterable[str]):
    """Speak a live stream of text chunks as they arrive via ElevenLabs.

    Raises TTSError if ElevenLabs isn't configured or the call fails — there
    is no fallback engine.
    """
    _require_api_key()
    try:
        asyncio.run(_elevenlabs_stream(text_chunks))
    except Exception as e:
        raise TTSError(f"ElevenLabs streaming TTS failed: {e}") from e


def speak(text: str):
    """Speak a single, already-complete chunk of text (used for one-off cases)."""
    if not text:
        return
    speak_stream(iter([text]))
