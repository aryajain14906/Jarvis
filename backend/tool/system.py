"""
tool/system.py
===============
Windows-specific system control.

Implemented: open_application, close_application, list_known_apps,
set_volume, take_screenshot.

Safety note: only apps in KNOWN_APPS/PROCESS_NAMES can be opened/closed. The
LLM picks a name, but it can only ever act on something explicitly
allow-listed here -- it can never pass an arbitrary path or command.

Extra installs needed for volume + screenshot:
    pip install pycaw comtypes pillow
"""

import os
import re
import subprocess
from datetime import datetime

# Map spoken/LLM-friendly names -> actual Windows executable names/protocols
# used to OPEN the app. Add to this list deliberately, one app at a time --
# don't allow-list anything you haven't tested opening.
KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "spotify": "spotify:",
}

# Separate mapping for CLOSING -- taskkill needs the real process image name,
# which isn't always the string used to launch the app (e.g. Spotify opens
# via the "spotify:" protocol but its process is Spotify.exe).
PROCESS_NAMES = {
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "task manager": "Taskmgr.exe",
    "control panel": "control.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "spotify": "Spotify.exe",
}


def list_known_apps(_: str = "") -> str:
    """List every app name JARVIS can currently open. Input: ignored."""
    return ", ".join(sorted(set(KNOWN_APPS.keys())))


def open_application(app_name: str) -> str:
    """Open a known Windows application by name. Input: an app name like 'notepad' or 'chrome'."""
    key = app_name.strip().lower()

    if key not in KNOWN_APPS:
        known = ", ".join(sorted(set(KNOWN_APPS.keys())))
        return f"I don't have '{app_name}' registered as an app I can open. I currently know: {known}."

    exe = KNOWN_APPS[key]
    try:
        # os.startfile uses Windows' App Paths registry / protocol handlers,
        # so it finds browsers and things like spotify: even when not on PATH.
        os.startfile(exe)
        return f"Opened {app_name}."
    except Exception:
        pass
    try:
        subprocess.Popen(exe, shell=True)
        return f"Opened {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"


def close_application(app_name: str) -> str:
    """Close a known Windows application by name. Input: an app name like 'notepad' or 'spotify'."""
    key = app_name.strip().lower()

    if key not in PROCESS_NAMES:
        known = ", ".join(sorted(set(PROCESS_NAMES.keys())))
        return f"I don't have '{app_name}' registered as a closeable app. I currently know: {known}."

    process_name = PROCESS_NAMES[key]
    try:
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return f"Closed {app_name}."
        combined = (result.stderr + result.stdout).lower()
        if "not found" in combined:
            return f"{app_name} doesn't appear to be running."
        return f"Couldn't close {app_name}: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return f"Error closing {app_name}: {e}"


def set_volume(level_str: str) -> str:
    """Set system volume. Input: a number 0-100, e.g. '30' or '30%'."""
    match = re.search(r"\d+", level_str)
    if not match:
        return f"I couldn't understand '{level_str}' as a volume level."
    level = max(0, min(100, int(match.group(0))))

    try:
        import comtypes
        from ctypes import cast, POINTER
        from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator, EDataFlow, ERole, CLSID_MMDeviceEnumerator

        # Some pycaw versions wrap AudioUtilities.GetSpeakers()'s result in their
        # own AudioDevice object, which has no .Activate() -- going straight to
        # the COM device enumerator avoids that wrapper entirely.
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%."
    except ImportError:
        return "Volume control needs pycaw installed -- run: pip install pycaw comtypes"
    except Exception as e:
        return f"Error setting volume: {e}"


def take_screenshot(_: str = "") -> str:
    """Take a screenshot and save it to disk. Input: ignored."""
    try:
        from PIL import ImageGrab

        folder = os.path.join(os.path.expanduser("~"), "Pictures", "JarvisScreenshots")
        os.makedirs(folder, exist_ok=True)
        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(folder, filename)

        img = ImageGrab.grab()
        img.save(path)
        return f"Screenshot saved to {path}."
    except ImportError:
        return "Screenshots need Pillow installed -- run: pip install pillow"
    except Exception as e:
        return f"Error taking screenshot: {e}"