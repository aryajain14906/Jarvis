"""
tts.py
======
Fully local, fully offline TTS via pyttsx3.

pyttsx3 wraps whatever speech engine your OS already has:
  - Windows -> SAPI5 (the same voices Web Speech API uses in Chrome/Edge)
  - macOS   -> NSSpeechSynthesizer
  - Linux   -> espeak (install with: sudo apt install espeak)

No internet call, no API key, nothing leaves the machine. Quality is more
robotic than edge-tts/Piper, but it's genuinely local.

Install:
    pip install pyttsx3
"""

import base64
import os
import tempfile

import pyttsx3


def _pick_voice(engine) -> None:
    """Try to land on a male-ish system voice for the JARVIS vibe."""
    for voice in engine.getProperty("voices"):
        name = voice.name.lower()
        if any(tag in name for tag in ("david", "mark", "male", "ryan", "george")):
            engine.setProperty("voice", voice.id)
            return
    # fall back to whatever the OS default is


def generate_jarvis_audio_base64(text: str) -> str:
    """Synthesizes `text` locally and returns Base64-encoded WAV audio."""
    engine = pyttsx3.init()
    _pick_voice(engine)
    engine.setProperty("rate", 165)   # slightly slower, measured delivery
    engine.setProperty("volume", 1.0)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        engine.save_to_file(text, tmp_path)
        engine.runAndWait()

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        return base64.b64encode(audio_bytes).decode("utf-8")
    finally:
        engine.stop()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)