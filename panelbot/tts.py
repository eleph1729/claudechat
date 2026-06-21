"""Text-to-speech via ElevenLabs, with macOS `say` as a no-key fallback.

`speak_stream()` is the main entry point: it takes an iterator of raw text
chunks (as they stream out of Claude — see `respond.py`) and feeds them
straight into ElevenLabs' websocket `stream-input` endpoint as they arrive,
playing back the synthesized audio as it streams back. There's no per-call
boundary the way there was with calling `convert_as_stream` once per
sentence — ElevenLabs keeps a single synthesis context for the whole
utterance, which is what was causing small glitches/resets at sentence
boundaries. The model itself decides how to chunk audio internally; we just
keep handing it text.

Audio comes back as raw 16-bit PCM (`output_format=pcm_16000`) so it can be
written straight to a `sounddevice` output stream with no decoding step.

If `ELEVENLABS_API_KEY` isn't set, we fall back to macOS `say`, sentence by
sentence (the websocket protocol is ElevenLabs-specific).
"""

import asyncio
import base64
import json
import re
import subprocess
from typing import Iterable

import sounddevice as sd
import websockets

import config

_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def _speak_say(text: str):
    cmd = ["say", "-r", str(config.TTS_RATE_WPM)]
    if config.TTS_VOICE:
        cmd += ["-v", config.TTS_VOICE]
    cmd.append(text)
    subprocess.run(cmd, check=False)


def _speak_say_stream(text_chunks: Iterable[str]):
    """Fallback path: group raw chunks into sentences and speak each via `say`."""
    buffer = ""
    for chunk in text_chunks:
        buffer += chunk
        while True:
            m = _SENTENCE_END.search(buffer)
            if not m:
                break
            sentence, buffer = buffer[:m.end()].strip(), buffer[m.end():]
            if sentence:
                _speak_say(sentence)
    tail = buffer.strip()
    if tail:
        _speak_say(tail)


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


def speak_stream(text_chunks: Iterable[str]):
    """Speak a live stream of text chunks as they arrive.

    Preferred path: feed them straight into ElevenLabs' websocket endpoint so
    there's a single synthesis context for the whole reply. Falls back to
    `say`, sentence by sentence, if no API key is configured or the websocket
    call fails outright.
    """
    if not config.ELEVENLABS_API_KEY:
        _speak_say_stream(text_chunks)
        return
    try:
        asyncio.run(_elevenlabs_stream(text_chunks))
    except Exception as e:
        print(f"  [tts] ElevenLabs streaming failed ({e}); reply already spoken/lost "
              f"for this turn.")


def speak(text: str):
    """Speak a single, already-complete chunk of text (used for one-off cases)."""
    if not text:
        return
    speak_stream(iter([text]))
