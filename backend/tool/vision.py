"""
tool/vision.py
================
Phase 6: screen understanding, split into two deliberately separate tools
based on what tonight's testing actually showed:

  - see_screen()       -> moondream (fast, ~7-8s), good for GIST-level
                           questions ("what app is open", "what kind of
                           window is this"). Confirmed to confidently
                           hallucinate specific text/labels -- never trust
                           it for exact wording or precise locations.
  - read_screen_text()  -> Tesseract OCR, for anything needing EXACT text
                           (error messages, button labels). OCR extracts
                           real pixels, not a plausible-sounding guess.

Screenshots here are captured fresh in-memory and never saved to disk --
unlike take_screenshot() in tool/system.py, which is an explicit on-demand
save. A screen can contain passwords/private messages/anything, so vision
captures shouldn't silently persist.

Requires:
    pip install pytesseract
    Tesseract OCR engine itself (NOT pip-installable) --
    https://github.com/UB-Mannheim/tesseract/wiki (free, open-source .exe
    installer for Windows). If it's not on PATH after installing, set
    TESSERACT_CMD_PATH below to the installed tesseract.exe location.
"""

import base64
import io

import requests
from PIL import ImageGrab

OLLAMA_URL = "http://localhost:11434/api/chat"
VISION_MODEL = "moondream"
MAX_VISION_WIDTH = 1280  # downscale before sending to the vision model -- speeds things up

# If Tesseract isn't automatically found on PATH after installing, uncomment
# and set this to your actual install path, e.g.:
# TESSERACT_CMD_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD_PATH = None


def _capture_screen():
    """Fresh in-memory screenshot -- never written to disk."""
    return ImageGrab.grab()


def see_screen(question: str) -> tuple[bool, str]:
    """
    Get a general (gist-level, NOT precise) description of what's on screen,
    optionally focused by a question. Input: a question about the screen, or
    empty string for a general description. Do NOT trust this for exact
    text/labels -- use read_screen_text for that.
    """
    try:
        img = _capture_screen()
        if img.width > MAX_VISION_WIDTH:
            ratio = MAX_VISION_WIDTH / img.width
            img = img.resize((MAX_VISION_WIDTH, int(img.height * ratio)))

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        prompt = question.strip() or "Describe what applications, windows, and general layout are visible on this screen."

        payload = {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
        }
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        description = response.json()["message"]["content"].strip()
        return True, description
    except requests.exceptions.ConnectionError:
        return False, "Can't reach Ollama for vision -- make sure it's running."
    except Exception as e:
        return False, f"Error analyzing screen: {e}"


def read_screen_text(_: str = "") -> tuple[bool, str]:
    """
    Extract the actual, exact text visible on screen via OCR. Use this
    (not see_screen) whenever exact wording matters -- error messages,
    button labels, any precise text. Input: ignored.
    """
    try:
        import pytesseract

        if TESSERACT_CMD_PATH:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD_PATH

        img = _capture_screen()  # full resolution, NOT downscaled -- OCR needs detail
        text = pytesseract.image_to_string(img).strip()

        if not text:
            return False, "No readable text found on screen."
        return True, text[:3000]
    except ImportError:
        return False, "OCR needs pytesseract installed -- run: pip install pytesseract (and install the Tesseract engine itself, see tool/vision.py's docstring)."
    except Exception as e:
        return False, f"Error reading screen text: {e}"


def _find_text_location(target: str):
    """
    Searches OCR word-level data for the target phrase, returns (x, y) center
    coordinates of the best match, or None if not found. This is the reliable
    path -- OCR gives real pixel boxes, not a guessed location.
    """
    import pytesseract
    from pytesseract import Output

    if TESSERACT_CMD_PATH:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD_PATH

    img = _capture_screen()
    data = pytesseract.image_to_data(img, output_type=Output.DICT)

    target_lower = target.strip().lower()
    words = data["text"]

    # Try matching consecutive words too (e.g. target "log in" split into
    # separate "Log" / "In" boxes), not just single-word matches.
    for window in (3, 2, 1):
        for i in range(len(words) - window + 1):
            phrase = " ".join(words[i:i + window]).strip().lower()
            if not phrase:
                continue
            if target_lower in phrase or phrase in target_lower:
                lefts = data["left"][i:i + window]
                tops = data["top"][i:i + window]
                widths = data["width"][i:i + window]
                heights = data["height"][i:i + window]
                x = min(lefts) + (max(l + w for l, w in zip(lefts, widths)) - min(lefts)) // 2
                y = min(tops) + (max(t + h for t, h in zip(tops, heights)) - min(tops)) // 2
                return (x, y), img
    return None, img


def _click_at(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


def _screens_differ(before, after, threshold: int = 15) -> bool:
    """
    Deterministic pixel-diff verification -- NOT asking the vision model
    "did it work", since tonight's testing proved it can't be trusted to
    judge that reliably. A real pixel difference is objective evidence
    something changed; the absence of one is objective evidence it didn't.
    """
    from PIL import ImageChops

    if before.size != after.size:
        return True
    diff = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    bbox = diff.getbbox()
    if bbox is None:
        return False
    # A tiny diff (e.g. a blinking cursor) shouldn't count as "something happened".
    diff_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    return diff_area > threshold * threshold


def click_on_screen(target: str) -> tuple[bool, str]:
    """
    EXPERIMENTAL: find and click a UI element by its visible text (Phase 6.5).
    Tries OCR first (reliable, exact text match) -- only falls back to a
    coarse vision-model guess for icon-only targets with no visible text,
    and that fallback is explicitly lower-confidence. Verifies the click via
    a real before/after pixel comparison, not by asking the vision model.
    Input: the text on the button/element to click, e.g. 'Login' or 'Save'.
    """
    target = target.strip()
    if not target:
        return False, "I need the text of what to click."

    try:
        location, before_img = _find_text_location(target)

        if location is None:
            # Fallback: no OCR match -- likely an icon-only target. Ask the
            # vision model for a rough region, but be honest this is a guess.
            ok, description = see_screen(
                f"In which rough area of the screen (e.g. top-left, top-right, "
                f"bottom-left, bottom-right, center) is '{target}' located?"
            )
            return False, (
                f"Couldn't find exact text '{target}' via OCR (likely an icon with no "
                f"label). Best guess from vision: {description if ok else 'unknown'}. "
                "Not confident enough to click blind -- try describing it differently, "
                "or this may need a person to click it."
            )

        x, y = location
        _click_at(x, y)

        import time
        time.sleep(0.6)  # let the UI actually respond before checking
        after_img = _capture_screen()

        if _screens_differ(before_img, after_img):
            return True, f"Clicked '{target}' at ({x}, {y}) -- the screen changed afterward, so it likely worked."
        return False, f"Clicked '{target}' at ({x}, {y}), but the screen looks unchanged -- it may not have worked."
    except ImportError as e:
        return False, f"click_on_screen needs pytesseract and pywin32 installed: {e}"
    except Exception as e:
        return False, f"Error clicking '{target}': {e}"