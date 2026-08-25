"""
tool/system.py
===============
Windows-specific system control. Phase 4.2B: open_application() only.
More capabilities (close_application, get_battery_status, set_volume,
mute, take_screenshot, get_system_info) get added one at a time in 4.2C+.

Safety note: only apps in KNOWN_APPS can be opened. The LLM picks a name,
but it can only ever launch something explicitly allow-listed here --
it can never pass an arbitrary path or command to actually execute.
"""

import os
import subprocess

# Map spoken/LLM-friendly names -> actual Windows executable names.
# Add to this list deliberately, one app at a time -- don't allow-list
# anything you haven't tested opening.
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


def list_known_apps(_: str = "") -> str:
    """List every app name JARVIS can currently open. Input: ignored."""
    names = sorted(set(KNOWN_APPS.keys()))
    return ", ".join(names)


def open_application(app_name: str) -> str:
    """
    Open a known Windows application by name. Input: an app name like
    'notepad' or 'chrome'. Returns a plain-text status JARVIS can speak.
    """
    key = app_name.strip().lower()

    if key not in KNOWN_APPS:
        known = ", ".join(sorted(set(KNOWN_APPS.keys())))
        return f"I don't have '{app_name}' registered as an app I can open. I currently know: {known}."

    exe = KNOWN_APPS[key]
    try:
        # os.startfile uses Windows' App Paths registry, so it finds browsers
        # like chrome/msedge/firefox even when they're not on PATH -- unlike
        # subprocess.Popen(exe, shell=True), which only checks PATH.
        os.startfile(exe)
        return f"Opened {app_name}."
    except Exception:
        pass
    try:
        subprocess.Popen(exe, shell=True)
        return f"Opened {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"