"""
tool/system.py
===============
Windows-specific system control.

Phase 4.4 update: open_application/close_application now VERIFY the action
actually happened via psutil (checking the real process list) instead of
just assuming success because no exception was thrown. Also adds
switch_application, get_running_applications, mute/get_volume, and
system/cpu/memory/battery info.

Extra installs needed:
    pip install pycaw comtypes pillow psutil pywin32
"""

import os
import re
import subprocess
import time
from datetime import datetime

import psutil

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
    "vs code": "code",
    "visual studio code": "code",
    "vscode": "code",
}

# Separate mapping for CLOSING/VERIFYING -- taskkill and psutil need the real
# process image name, which isn't always the string used to launch the app
# (e.g. Spotify opens via the "spotify:" protocol but its process is Spotify.exe).
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
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "vscode": "Code.exe",
}


def _is_process_running(process_name: str) -> bool:
    """Real verification via the actual process list, not an assumption."""
    process_name = process_name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == process_name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def list_known_apps(_: str = "") -> tuple[bool, str]:
    """List every app name JARVIS can currently open. Input: ignored."""
    return True, ", ".join(sorted(set(KNOWN_APPS.keys())))


def open_application(app_name: str) -> tuple[bool, str]:
    """Open a known Windows application by name. Input: an app name like 'notepad' or 'chrome'."""
    key = app_name.strip().lower()

    if key not in KNOWN_APPS:
        known = ", ".join(sorted(set(KNOWN_APPS.keys())))
        return False, f"'{app_name}' isn't registered as an app I can open. I currently know: {known}."

    exe = KNOWN_APPS[key]
    launched = False
    try:
        # os.startfile uses Windows' App Paths registry / protocol handlers,
        # so it finds browsers and things like spotify: even when not on PATH.
        os.startfile(exe)
        launched = True
    except Exception:
        try:
            subprocess.Popen(exe, shell=True)
            launched = True
        except Exception as e:
            return False, f"Failed to open {app_name}: {e}"

    if not launched:
        return False, f"Failed to open {app_name}."

    # Verify it actually opened -- don't just trust that the launch command
    # didn't throw. Some apps take a moment to appear in the process list.
    process_name = PROCESS_NAMES.get(key)
    if process_name:
        for _ in range(6):  # up to ~3 seconds
            if _is_process_running(process_name):
                return True, f"Opened {app_name}."
            time.sleep(0.5)
        return False, f"I tried to open {app_name}, but I can't confirm it's actually running."

    # No verification mapping for this app -- report the launch attempt honestly.
    return True, f"Opened {app_name} (launched, but I can't independently verify it's running)."


def close_application(app_name: str) -> tuple[bool, str]:
    """Close a known Windows application by name. Input: an app name like 'notepad' or 'spotify'."""
    key = app_name.strip().lower()

    if key not in PROCESS_NAMES:
        known = ", ".join(sorted(set(PROCESS_NAMES.keys())))
        return False, f"'{app_name}' isn't registered as a closeable app. I currently know: {known}."

    process_name = PROCESS_NAMES[key]
    if not _is_process_running(process_name):
        return True, f"{app_name} doesn't appear to be running."

    try:
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True, text=True, timeout=10,
        )
        time.sleep(0.5)
        if not _is_process_running(process_name):
            return True, f"Closed {app_name}."
        return False, f"Tried to close {app_name}, but it still appears to be running."
    except Exception as e:
        return False, f"Error closing {app_name}: {e}"


def get_running_applications(_: str = "") -> tuple[bool, str]:
    """List currently running applications from the known app list. Input: ignored."""
    # Multiple aliases (e.g. "edge" and "microsoft edge") map to the SAME
    # process -- dedupe by process name first so it isn't reported twice.
    seen_processes = set()
    running = []
    for name, process_name in PROCESS_NAMES.items():
        if process_name in seen_processes:
            continue
        if _is_process_running(process_name):
            running.append(name)
            seen_processes.add(process_name)
    if not running:
        return True, "None of the apps I know about appear to be running right now."
    return True, ", ".join(sorted(running))


def get_disk_usage(_: str = "") -> tuple[bool, str]:
    """Get free/used/total disk space on the main drive. Input: ignored."""
    try:
        import shutil as _shutil

        total, used, free = _shutil.disk_usage(os.path.expanduser("~")[:3] or "C:\\")
        total_gb = round(total / (1024 ** 3), 1)
        used_gb = round(used / (1024 ** 3), 1)
        free_gb = round(free / (1024 ** 3), 1)
        return True, f"Disk: {free_gb} GB free of {total_gb} GB total ({used_gb} GB used)."
    except Exception as e:
        return False, f"Error reading disk usage: {e}"


def switch_application(app_name: str) -> tuple[bool, str]:
    """Bring a running application's window to the foreground. Input: an app name like 'notepad'."""
    key = app_name.strip().lower()
    if key not in PROCESS_NAMES:
        return False, f"'{app_name}' isn't a registered app."

    process_name = PROCESS_NAMES[key]
    if not _is_process_running(process_name):
        return False, f"{app_name} doesn't appear to be running -- nothing to switch to."

    try:
        import win32gui
        import win32process

        target_pids = {p.pid for p in psutil.process_iter(["name"]) if p.info["name"] and p.info["name"].lower() == process_name.lower()}

        pid_matches = []
        title_matches = []

        def _enum_handler(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in target_pids:
                pid_matches.append(hwnd)
            # Fallback: some apps (notably console apps like cmd.exe) don't
            # own their own visible window -- it's hosted by conhost.exe or
            # Windows Terminal instead. A title match catches these.
            elif key in title.lower() or app_name.lower() in title.lower():
                title_matches.append(hwnd)

        win32gui.EnumWindows(_enum_handler, None)
        found = pid_matches or title_matches
        if not found:
            return False, f"{app_name} is running, but I couldn't find its window to switch to."

        target_hwnd = found[0]
        win32gui.ShowWindow(target_hwnd, 9)  # SW_RESTORE

        # SetForegroundWindow silently fails (with a misleading error) when
        # called from a background process -- Windows' anti focus-stealing
        # protection blocks it unless the caller's input thread is attached
        # to the target window's thread first. This is the standard bypass.
        try:
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            import win32api
            import win32con

            current_thread = win32api.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(target_hwnd)
            win32process.AttachThreadInput(current_thread, target_thread, True)
            try:
                win32gui.SetForegroundWindow(target_hwnd)
            finally:
                win32process.AttachThreadInput(current_thread, target_thread, False)

        return True, f"Switched to {app_name}."
    except ImportError as e:
        return False, "Switching windows needs pywin32 installed -- run: pip install pywin32"
    except Exception as e:
        return False, f"Error switching to {app_name}: {e}"


def mute(_: str = "") -> tuple[bool, str]:
    """Mute or unmute system audio (toggle). Input: ignored."""
    try:
        import comtypes
        from ctypes import cast, POINTER
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole

        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        currently_muted = volume.GetMute()
        volume.SetMute(0 if currently_muted else 1, None)
        return True, "Unmuted." if currently_muted else "Muted."
    except ImportError as e:
        return False, f"Mute import error (pycaw version mismatch?): {e}"
    except Exception as e:
        return False, f"Error toggling mute: {e}"


def get_volume(_: str = "") -> tuple[bool, str]:
    """Get the current system volume level. Input: ignored."""
    try:
        import comtypes
        from ctypes import cast, POINTER
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole

        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        level = round(volume.GetMasterVolumeLevelScalar() * 100)
        return True, f"Volume is at {level}%."
    except ImportError as e:
        return False, f"Volume reading import error (pycaw version mismatch?): {e}"
    except Exception as e:
        return False, f"Error reading volume: {e}"


def set_volume(level_str: str) -> tuple[bool, str]:
    """Set system volume. Input: a number 0-100, e.g. '30' or '30%'."""
    match = re.search(r"\d+", level_str)
    if not match:
        return False, f"Couldn't understand '{level_str}' as a volume level."
    level = max(0, min(100, int(match.group(0))))

    try:
        import comtypes
        from ctypes import cast, POINTER
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole

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
        return True, f"Volume set to {level}%."
    except ImportError as e:
        return False, f"Volume control import error (pycaw version mismatch?): {e}"
    except Exception as e:
        return False, f"Error setting volume: {e}"


def system_info(_: str = "") -> tuple[bool, str]:
    """Get basic system info (OS, CPU, RAM total). Input: ignored."""
    try:
        import platform

        info = (
            f"{platform.system()} {platform.release()}, "
            f"{psutil.cpu_count(logical=True)} logical CPUs, "
            f"{round(psutil.virtual_memory().total / (1024 ** 3), 1)} GB RAM total."
        )
        return True, info
    except Exception as e:
        return False, f"Error getting system info: {e}"


def cpu_usage(_: str = "") -> tuple[bool, str]:
    """Get current CPU usage percentage. Input: ignored."""
    try:
        percent = psutil.cpu_percent(interval=0.5)
        return True, f"CPU usage is at {percent}%."
    except Exception as e:
        return False, f"Error reading CPU usage: {e}"


def memory_usage(_: str = "") -> tuple[bool, str]:
    """Get current RAM usage. Input: ignored."""
    try:
        mem = psutil.virtual_memory()
        used_gb = round(mem.used / (1024 ** 3), 1)
        total_gb = round(mem.total / (1024 ** 3), 1)
        return True, f"Memory usage: {used_gb} GB of {total_gb} GB ({mem.percent}%)."
    except Exception as e:
        return False, f"Error reading memory usage: {e}"


def battery_status(_: str = "") -> tuple[bool, str]:
    """Get battery percentage and charging status, if the device has a battery. Input: ignored."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return False, "No battery detected -- this device may be a desktop."
        status = "charging" if battery.power_plugged else "on battery"
        return True, f"Battery is at {battery.percent}% and {status}."
    except Exception as e:
        return False, f"Error reading battery status: {e}"


def take_screenshot(_: str = "") -> tuple[bool, str]:
    """Take a screenshot and save it to disk. Input: ignored."""
    try:
        from PIL import ImageGrab

        folder = os.path.join(os.path.expanduser("~"), "Pictures", "JarvisScreenshots")
        os.makedirs(folder, exist_ok=True)
        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(folder, filename)

        img = ImageGrab.grab()
        img.save(path)
        return True, f"Screenshot saved to {path}."
    except ImportError as e:
        return False, "Screenshots need Pillow installed -- run: pip install pillow"
    except Exception as e:
        return False, f"Error taking screenshot: {e}"