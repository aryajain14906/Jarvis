"""
tool/filesystem.py
====================
Phase 4.2C: basic filesystem access.

Multi-argument tools (create_file, write_file, move_file, copy_file) take
their input as a JSON string, e.g.:
    '{"path": "C:/Users/you/Desktop/notes.txt", "content": "hello"}'
The agent's tool description tells the model to format input this way.

Safety note: full permission levels come in 4.2F. For now there's just a
basic blocklist so the agent can't be tricked into touching core Windows
system folders. Don't rely on this alone for anything sensitive yet.
"""

import json
import os
import shutil

# Minimal guardrail until 4.2F adds real permission levels.
_BLOCKED_SUBSTRINGS = ["windows\\system32", "program files", "\\windows\\"]


def _is_blocked(path: str) -> bool:
    normalized = os.path.abspath(path).lower()
    return any(b in normalized for b in _BLOCKED_SUBSTRINGS)


def search_files(query: str, root: str = None) -> str:
    """Search for files by name (substring match) under the user's home directory."""
    root = root or os.path.expanduser("~")
    query_lower = query.strip().lower()
    matches = []
    for dirpath, _, filenames in os.walk(root):
        # Skip noisy/system-ish directories to keep this fast and relevant.
        if any(skip in dirpath.lower() for skip in ["\\.git", "node_modules", "__pycache__", "\\venv"]):
            continue
        for fname in filenames:
            if query_lower in fname.lower():
                matches.append(os.path.join(dirpath, fname))
                if len(matches) >= 15:
                    return "\n".join(matches)
    return "\n".join(matches) if matches else f"No files found matching '{query}'."


def read_file(path: str) -> str:
    """Read and return a text file's contents (capped at 4000 chars)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(4000)
        return content if content else "(file is empty)"
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


def create_file(tool_input: str) -> str:
    """Create a new file. Input: JSON with 'path' and 'content' (content optional)."""
    try:
        data = json.loads(tool_input)
        path = data["path"]
        content = data.get("content", "")
    except Exception as e:
        return f"Error: expected JSON with 'path' (and optional 'content'). {e}"

    if _is_blocked(path):
        return "Refusing: that path is in a protected system location."
    if os.path.exists(path):
        return f"File already exists: {path}. Use write_file to modify it."
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Created {path}."
    except Exception as e:
        return f"Error creating file: {e}"


def write_file(tool_input: str) -> str:
    """Overwrite an existing file's contents. Input: JSON with 'path' and 'content'."""
    try:
        data = json.loads(tool_input)
        path = data["path"]
        content = data["content"]
    except Exception as e:
        return f"Error: expected JSON with 'path' and 'content'. {e}"

    if _is_blocked(path):
        return "Refusing: that path is in a protected system location."
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} characters to {path}."
    except Exception as e:
        return f"Error writing file: {e}"


def move_file(tool_input: str) -> str:
    """Move a file. Input: JSON with 'source' and 'destination'."""
    try:
        data = json.loads(tool_input)
        src, dst = data["source"], data["destination"]
    except Exception as e:
        return f"Error: expected JSON with 'source' and 'destination'. {e}"

    if _is_blocked(src) or _is_blocked(dst):
        return "Refusing: source or destination is in a protected system location."
    try:
        shutil.move(src, dst)
        return f"Moved {src} to {dst}."
    except Exception as e:
        return f"Error moving file: {e}"


def copy_file(tool_input: str) -> str:
    """Copy a file. Input: JSON with 'source' and 'destination'."""
    try:
        data = json.loads(tool_input)
        src, dst = data["source"], data["destination"]
    except Exception as e:
        return f"Error: expected JSON with 'source' and 'destination'. {e}"

    if _is_blocked(src) or _is_blocked(dst):
        return "Refusing: source or destination is in a protected system location."
    try:
        shutil.copy2(src, dst)
        return f"Copied {src} to {dst}."
    except Exception as e:
        return f"Error copying file: {e}"