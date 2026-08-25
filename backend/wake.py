"""
wake.py
=======
Standalone, always-on JARVIS listener. Run this as its own process --
it does NOT need the browser tab open at all. It owns the microphone
directly, listens for "Hey Jarvis," records your command, transcribes
it locally, sends it to your existing FastAPI backend, and speaks the
reply directly (no HTTP round-trip for audio -- it's already a local
Python process, so it just uses pyttsx3 in-process).

Requires app.py + Ollama already running (this script only talks to
your own /chat endpoint over localhost).

TODO (before calling the project done): add barge-in support -- right now
saying "Hey Jarvis" while it's mid-reply does nothing, because listening
and speaking happen sequentially, not concurrently. Needs: (1) mic reading
moved to its own thread so it's active during playback, (2) engine.stop()
wired to fire the moment the wake word re-triggers, (3) a short guard
window after speech starts so JARVIS doesn't hear and react to its own
voice through the speakers.

Install (one-time):
    pip install openwakeword onnxruntime sounddevice faster-whisper pyttsx3 requests

First run downloads the pretrained wake-word models (~few MB, one-time,
needs internet just for that download) and the faster-whisper model
(~150MB for base.en, also one-time).
"""

import io
import time
import wave

import numpy as np
import openwakeword
import sounddevice as sd
import pyttsx3
import requests
from faster_whisper import WhisperModel
from openwakeword.model import Model

try:
    import winsound  # Windows-only, used for the "listening" beep
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
CHAT_URL = "http://127.0.0.1:8000/chat"

RATE = 16000
CHUNK = 1280            # openWakeWord's recommended frame size
WAKE_THRESHOLD = 0.5    # lower = more sensitive (more false triggers)

MAX_COMMAND_SECONDS = 7      # hard cap on how long you can talk after the beep
SILENCE_CUTOFF_SECONDS = 1.1  # stop recording after this much trailing silence
SILENCE_VOLUME_THRESHOLD = 350  # tune this up if it cuts you off mid-sentence,
                                 # or down if it never stops recording

WHISPER_MODEL_SIZE = "base.en"  # try "small.en" for better accuracy, "tiny.en" for more speed
WHISPER_DEVICE = "cpu"          # switch to "cuda" if you've got CUDA + cuDNN set up


# ------------------------------------------------------------------
# SETUP
# ------------------------------------------------------------------
def beep():
    if HAS_WINSOUND:
        winsound.Beep(880, 150)
    else:
        print("\a", end="", flush=True)  # terminal bell fallback on mac/linux


print("Loading wake word model...")
openwakeword.utils.download_models()
oww_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

print(f"Loading Whisper ({WHISPER_MODEL_SIZE}, {WHISPER_DEVICE})...")
whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type="int8")

print("Initializing local TTS engine...")
tts_engine = pyttsx3.init()
for voice in tts_engine.getProperty("voices"):
    if any(tag in voice.name.lower() for tag in ("david", "mark", "male", "ryan", "george")):
        tts_engine.setProperty("voice", voice.id)
        break
tts_engine.setProperty("rate", 165)


def speak(text: str) -> None:
    print(f"JARVIS: {text}")
    tts_engine.say(text)
    tts_engine.runAndWait()


# ------------------------------------------------------------------
# AUDIO CAPTURE (sounddevice)
# ------------------------------------------------------------------
stream = sd.InputStream(
    samplerate=RATE,
    channels=1,
    dtype="int16",
    blocksize=CHUNK,
)
stream.start()


def read_chunk() -> bytes:
    """Reads one CHUNK-sized frame and returns raw 16-bit PCM bytes."""
    data, overflowed = stream.read(CHUNK)
    if overflowed:
        print("Warning: input overflowed, a bit of audio was dropped.")
    return data.tobytes()


def record_command() -> bytes:
    """Records from the same open stream until silence or the time cap hits."""
    frames = []
    silence_chunks = 0
    started_talking = False
    max_chunks = int(RATE / CHUNK * MAX_COMMAND_SECONDS)
    silence_chunk_limit = int(RATE / CHUNK * SILENCE_CUTOFF_SECONDS)

    for _ in range(max_chunks):
        data = read_chunk()
        frames.append(data)

        volume = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
        if volume > SILENCE_VOLUME_THRESHOLD:
            started_talking = True
            silence_chunks = 0
        elif started_talking:
            silence_chunks += 1
            if silence_chunks > silence_chunk_limit:
                break

    return b"".join(frames)


def transcribe(raw_pcm: bytes) -> str:
    # faster-whisper wants a file (or file-like object), so wrap the raw
    # PCM frames in an in-memory WAV container -- no disk I/O needed.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(RATE)
        wf.writeframes(raw_pcm)
    buf.seek(0)

    segments, _info = whisper_model.transcribe(buf, language="en")
    return " ".join(seg.text for seg in segments).strip()


def ask_backend(question: str) -> str:
    try:
        resp = requests.post(CHAT_URL, json={"question": question}, timeout=120)
        resp.raise_for_status()
        return resp.json().get("answer", "")
    except requests.exceptions.ConnectionError:
        return "I can't reach the backend, sir. Is app.py running?"
    except Exception as e:
        return f"Something went wrong: {e}"


# ------------------------------------------------------------------
# MAIN LOOP
# ------------------------------------------------------------------
def main():
    print('Listening for "Hey Jarvis"... (Ctrl+C to quit)')
    try:
        while True:
            chunk = read_chunk()
            audio_np = np.frombuffer(chunk, dtype=np.int16)
            prediction = oww_model.predict(audio_np)

            # Check any key containing "jarvis" -- pretrained model naming
            # has varied slightly across openWakeWord versions.
            triggered = any(
                score > WAKE_THRESHOLD
                for name, score in prediction.items()
                if "jarvis" in name.lower()
            )

            if triggered:
                print("Wake word detected.")
                beep()
                oww_model.reset()  # clear internal buffers so it doesn't retrigger

                raw_audio = record_command()
                text = transcribe(raw_audio)

                if not text:
                    speak("I didn't catch that, sir.")
                    continue

                print(f"You: {text}")
                answer = ask_backend(text)
                speak(answer)

                print('\nListening for "Hey Jarvis"...')
                time.sleep(0.3)  # brief cooldown before resuming detection

    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    main()