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
import collections
import json
import threading
import time
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
    start = time.monotonic()
    sr = config.TTS_SAMPLE_RATE
    uri = (f"wss://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
           f"/stream-input?model_id={config.ELEVENLABS_MODEL}&output_format=pcm_{sr}")

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
                # Flush the first audio chunk after a small amount of text
                # instead of ElevenLabs' ~120-char default — this is the main
                # lever on time-to-first-speech.
                "generation_config": {
                    "chunk_length_schedule": config.ELEVENLABS_CHUNK_SCHEDULE,
                },
            }))

            async def sender():
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        await ws.send(json.dumps({"text": ""}))
                        break
                    await ws.send(json.dumps({"text": chunk}))

            async def receiver():
                # Jitter buffer: incoming PCM chunks queue in a deque, and a
                # PortAudio callback thread drains them for playback. This
                # decouples network arrival from playback rate — without it,
                # a blocking write() runs the audio device dry between chunks
                # (an underrun heard as a click/glitch at chunk boundaries).
                #
                # The callback must finish well within its realtime deadline or
                # the audio crackles, so it does the minimum: pop whole chunks
                # and track a read offset into the front one (no per-call
                # memmove), under a lock held only for fast memory copies. The
                # producer base64-decodes OUTSIDE the lock so it never stalls
                # the callback.
                bytes_per_frame = 2  # int16 mono
                prime_bytes = int(config.TTS_PRIME_SECONDS * sr) * bytes_per_frame
                pending: "collections.deque[bytes]" = collections.deque()
                head = b""          # partially-consumed front chunk
                hpos = 0            # read offset into head
                buffered = 0        # total bytes available across head + pending
                underflows = 0
                lock = threading.Lock()

                def callback(outdata, frames, time_info, status):
                    nonlocal head, hpos, buffered, underflows
                    need = frames * bytes_per_frame
                    written = 0
                    with lock:
                        while written < need:
                            if hpos >= len(head):
                                if not pending:
                                    break
                                head, hpos = pending.popleft(), 0
                            take = min(need - written, len(head) - hpos)
                            outdata[written:written + take] = head[hpos:hpos + take]
                            hpos += take
                            written += take
                            buffered -= take
                    if written < need:      # underrun -> brief silence, not a click
                        outdata[written:] = b"\x00" * (need - written)
                        underflows += 1

                # latency="high" gives CoreAudio a roomier device buffer, which
                # together with the prime lead keeps the stream from starving
                # (audible as crackle) under network jitter.
                stream = sd.RawOutputStream(samplerate=sr, channels=1,
                                            dtype="int16", latency="high",
                                            callback=callback)
                started = False
                first_audio = True
                try:
                    async for message in ws:
                        data = json.loads(message)
                        audio_b64 = data.get("audio")
                        if audio_b64:
                            if first_audio:
                                first_audio = False
                                if config.TTS_TIMING:
                                    print(f"  [tts] first audio in "
                                          f"{time.monotonic() - start:.2f}s")
                            pcm = base64.b64decode(audio_b64)   # decode off-lock
                            with lock:
                                pending.append(pcm)
                                buffered += len(pcm)
                                ready = buffered
                            # Prime a lead before playback so early inter-chunk
                            # gaps don't immediately underrun.
                            if not started and ready >= prime_bytes:
                                stream.start()
                                started = True
                        if data.get("isFinal"):
                            break

                    if not started:        # short reply: never hit prime threshold
                        stream.start()
                        started = True
                    # Let the buffer drain fully before tearing down, or the
                    # tail of the reply gets cut off.
                    while True:
                        with lock:
                            remaining = buffered
                        if remaining <= 0:
                            break
                        await asyncio.sleep(0.05)
                    if config.TTS_TIMING and underflows:
                        print(f"  [tts] {underflows} buffer underflow(s) this reply")
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
