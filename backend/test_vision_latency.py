"""
test_vision_latency.py
========================
Standalone vision test, no agent integration -- verifies:
  1. Screenshot -> base64 encoding is correct (saves a copy to disk so you
     can visually confirm what was actually sent, ruling out a capture bug).
  2. Ollama's /api/chat is called with the correct "images" field.
  3. Timing for the full round trip.

Run with different MODEL_NAME values to compare llava vs moondream directly.
"""

import base64
import io
import time

import requests
from PIL import ImageGrab

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "moondream"  # change to "llava:7b" to compare
MAX_WIDTH = 1280  # downscale before sending -- speeds things up, minimal accuracy loss


def capture_and_encode() -> str:
    img = ImageGrab.grab()

    # Downscale if wider than MAX_WIDTH, preserving aspect ratio.
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)))

    # Save a copy to disk so you can SEE exactly what was captured/sent --
    # this is the key check for ruling out a capture/encoding bug.
    img.save("test_vision_capture.png")
    print("Saved what was actually captured to test_vision_capture.png -- open it and compare to your real screen.")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def ask_vision(question: str) -> str:
    print("Taking screenshot...")
    t0 = time.time()
    image_b64 = capture_and_encode()
    print(f"Screenshot + encode: {time.time() - t0:.2f}s")

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": question,
                "images": [image_b64],  # Ollama's actual expected field for vision models
            }
        ],
        "stream": False,
    }

    print(f"Sending to {MODEL_NAME}...")
    t1 = time.time()
    response = requests.post(OLLAMA_URL, json=payload, timeout=180)
    response.raise_for_status()
    elapsed = time.time() - t1
    print(f"Vision inference: {elapsed:.2f}s")

    data = response.json()
    return data["message"]["content"]


if __name__ == "__main__":
    total_start = time.time()
    answer = ask_vision("Describe exactly what applications, windows, and icons are visible on this screen.")
    print(f"\n{MODEL_NAME} response:\n{answer}")
    print(f"\nTotal time: {time.time() - total_start:.2f}s")