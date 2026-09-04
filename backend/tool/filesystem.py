"""
tool/filesystem.py
====================
Basic filesystem access.

Phase 4.2E update: every function now returns (ok: bool, message: str)
instead of a bare string.

Multi-argument tools (create_file, write_file, move_file, copy_file) take
their input as a JSON string, e.g.:
    '{"path": "C:/Users/you/Desktop/notes.txt", "content": "hello"}'

Safety note: full permission levels come in 4.2F. For now there's just a
basic blocklist so the agent can't be tricked into touching core Windows
system folders.
"""

import json
import os
import shutil
import time

_BLOCKED_SUBSTRINGS = ["windows\\system32", "program files", "\\windows\\"]


def _is_blocked(path: str) -> bool:
    normalized = os.path.abspath(path).lower()
    return any(b in normalized for b in _BLOCKED_SUBSTRINGS)


def search_files(query: str, root: str = None) -> tuple[bool, str]:
    """Search for files by name (substring match) under the user's home directory."""
    root = root or os.path.expanduser("~")
    query_lower = query.strip().lower()
    matches = []
    start = time.time()
    TIME_BUDGET_SECONDS = 8  # stop even without matches -- a 60s hang is worse than an honest "try narrowing it"

    for dirpath, _, filenames in os.walk(root):
        if time.time() - start > TIME_BUDGET_SECONDS:
            if matches:
                return True, "\n".join(matches) + "\n(stopped early -- there may be more, try narrowing the search)"
            return False, f"Search took too long without finding '{query}' -- try a more specific name or folder."

        if any(skip in dirpath.lower() for skip in [
            "\\.git", "node_modules", "__pycache__", "\\venv",
            "\\appdata", "\\onedrive", "\\programdata", "\\windows\\",
        ]):
            continue
        for fname in filenames:
            if query_lower in fname.lower():
                matches.append(os.path.join(dirpath, fname))
                if len(matches) >= 15:
                    return True, "\n".join(matches)
    if matches:
        return True, "\n".join(matches)
    return False, f"No files found matching '{query}'."


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".json", ".csv", ".log", ".html", ".css",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".bat", ".ps1", ".sh",
}
_KNOWN_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pdf", ".exe", ".dll",
    ".zip", ".rar", ".7z", ".mp3", ".mp4", ".wav", ".pyc",
}


def read_file(path: str) -> tuple[bool, str]:
    """Read and return a text file's contents (capped at 4000 chars). Refuses binary files."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _KNOWN_BINARY_EXTENSIONS:
        return False, f"{path} is a binary file ({ext}) -- I can only read text files, not open/view binary content like images or executables."

    try:
        with open(path, "rb") as f:
            head = f.read(1024)
        # Null bytes are a strong signal of binary content even for
        # extensions not in the known-binary list above.
        if b"\x00" in head:
            return False, f"{path} looks like a binary file, not text -- I can't read this one."

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(4000)
        if not content:
            return True, "(file is empty)"
        return True, content
    except FileNotFoundError:
        return False, f"File not found: {path}"
    except Exception as e:
        return False, f"Error reading file: {e}"


def create_file(tool_input: str) -> tuple[bool, str]:
    """Create a new file. Input: JSON with 'path' and 'content' (content optional)."""
    try:
        data = json.loads(tool_input)
        path = data["path"]
        content = data.get("content", "")
    except Exception as e:
        return False, f"Expected JSON with 'path' (and optional 'content'). {e}"

    if _is_blocked(path):
        return False, "Refused: that path is in a protected system location."
    if os.path.exists(path):
        return False, f"File already exists: {path}. Use write_file to modify it."
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"Created {path}."
    except Exception as e:
        return False, f"Error creating file: {e}"


def write_file(tool_input: str) -> tuple[bool, str]:
    """Overwrite an existing file's contents. Input: JSON with 'path' and 'content'."""
    try:
        data = json.loads(tool_input)
        path = data["path"]
        content = data["content"]
    except Exception as e:
        return False, f"Expected JSON with 'path' and 'content'. {e}"

    if _is_blocked(path):
        return False, "Refused: that path is in a protected system location."
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"Wrote {len(content)} characters to {path}."
    except Exception as e:
        return False, f"Error writing file: {e}"


def move_file(tool_input: str) -> tuple[bool, str]:
    """Move a file. Input: JSON with 'source' and 'destination'."""
    try:
        data = json.loads(tool_input)
        src, dst = data["source"], data["destination"]
    except Exception as e:
        return False, f"Expected JSON with 'source' and 'destination'. {e}"

    if _is_blocked(src) or _is_blocked(dst):
        return False, "Refused: source or destination is in a protected system location."
    try:
        shutil.move(src, dst)
        return True, f"Moved {src} to {dst}."
    except Exception as e:
        return False, f"Error moving file: {e}"


def copy_file(tool_input: str) -> tuple[bool, str]:
    """Copy a file. Input: JSON with 'source' and 'destination'."""
    try:
        data = json.loads(tool_input)
        src, dst = data["source"], data["destination"]
    except Exception as e:
        return False, f"Expected JSON with 'source' and 'destination'. {e}"

    if _is_blocked(src) or _is_blocked(dst):
        return False, "Refused: source or destination is in a protected system location."
    try:
        shutil.copy2(src, dst)
        return True, f"Copied {src} to {dst}."
    except Exception as e:
        return False, f"Error copying file: {e}"


def delete_file(path: str) -> tuple[bool, str]:
    """Delete a file. Input: the full file path. This is destructive -- confirmation is enforced separately by permissions.json."""
    if _is_blocked(path):
        return False, "Refused: that path is in a protected system location."
    if not os.path.exists(path):
        return False, f"File not found: {path}"
    try:
        os.remove(path)
        # Verify -- don't just trust that os.remove() not raising means it worked.
        if os.path.exists(path):
            return False, f"Tried to delete {path}, but it still exists."
        return True, f"Deleted {path}."
    except Exception as e:
        return False, f"Error deleting file: {e}"


def rename_file(tool_input: str) -> tuple[bool, str]:
    """Rename a file. Input: JSON with 'path' and 'new_name' (just the filename, not a full path)."""
    try:
        data = json.loads(tool_input)
        path = data["path"]
        new_name = data["new_name"]
    except Exception as e:
        return False, f"Expected JSON with 'path' and 'new_name'. {e}"

    if _is_blocked(path):
        return False, "Refused: that path is in a protected system location."
    if not os.path.exists(path):
        return False, f"File not found: {path}"

    new_path = os.path.join(os.path.dirname(path), new_name)
    if _is_blocked(new_path):
        return False, "Refused: the new path is in a protected system location."
    try:
        os.rename(path, new_path)
        # Verify -- confirm the new name exists and the old one doesn't.
        if os.path.exists(new_path) and not os.path.exists(path):
            return True, f"Renamed {path} to {new_path}."
        return False, f"Tried to rename {path}, but the result doesn't look right."
    except Exception as e:
        return False, f"Error renaming file: {e}"