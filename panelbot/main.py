"""Main loop: listen continuously, decide when to speak, speak.

    mic ── utterances ──> STT ──> transcript ──> decide ──> Claude (streamed) ──> TTS

Run with:  python -m panelbot.main
Stop with: Ctrl-C
"""

import queue
import time

import config
from panelbot import prosody, tts
from panelbot.audio import Microphone
from panelbot.decide import Decision, TurnTaker
from panelbot.respond import Responder
from panelbot.stt import Transcriber


def main():
    print("Loading speech-to-text model...")
    stt = Transcriber()
    responder = Responder()
    turns = TurnTaker()
    mic = Microphone()

    print(f"{config.BOT_NAME} is listening. Say '{config.BOT_NAME}' to address it "
          f"directly, or pause for {config.SILENCE_TO_SPEAK:.0f}s to invite a "
          f"contribution. Ctrl-C to quit.\n")
    mic.start()

    try:
        while True:
            # Drain any completed utterances and transcribe them.
            latest = ""
            latest_prosody = None
            try:
                while True:
                    pcm = mic.utterances.get_nowait()
                    text = stt.transcribe(pcm)
                    if text:
                        print(f"  heard: {text}")
                        responder.add_heard(text)
                        latest = text
                        latest_prosody = prosody.extract(pcm)
            except queue.Empty:
                pass

            decision = turns.decide(latest, latest_prosody,
                                    mic.seconds_since_voice,
                                    responder.transcript_text())

            if decision == Decision.SPEAK:
                mic.speaking.set()          # mute input while we talk
                spoke = False
                try:
                    for sentence in responder.reply_stream(turns.last_note):
                        if not spoke:
                            print(f"{config.BOT_NAME}: ", end="", flush=True)
                            spoke = True
                        print(sentence, end=" ", flush=True)
                        tts.speak(sentence)     # blocks until this sentence is spoken
                finally:
                    mic.speaking.clear()
                if spoke:
                    print("\n")
                    turns.note_bot_spoke()

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping.")
        mic.stop()


if __name__ == "__main__":
    main()
