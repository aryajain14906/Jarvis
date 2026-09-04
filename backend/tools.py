"""
JARVIS Tool registry -- Phase 4.2E.

Every tool function now returns (ok: bool, message: str) instead of a bare
string. This lets agent.py tell a real failure apart from a normal result
without string-matching on "Error", and skip handing failures to the
phrasing LLM (which is exactly how embellished/fabricated answers happened
before -- a vague error string got "phrased naturally" into something that
sounded like a real result).

run_tool() also logs every call (timestamp, tool, input, ok, message) to
tool_calls.log so there's a real audit trail, not just whatever's still
scrolled in the terminal.
"""

import ast
import json
import operator
import time
from datetime import datetime

from tool.system import (
    open_application, close_application, list_known_apps, set_volume, take_screenshot,
    get_running_applications, switch_application, mute, get_volume,
    system_info, cpu_usage, memory_usage, battery_status, get_disk_usage,
)
from tool.filesystem import search_files, read_file, create_file, write_file, move_file, copy_file, delete_file, rename_file
from tool.browser import open_browser, navigate, click, type_text, read_page, close_browser
from tool.web import web_search, fetch_url
from tool.memory import remember_fact
from tool.audit import recent_actions
from tool.weather import get_weather

# ---- Safe arithmetic evaluator (no eval()) ----
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Unsupported constant")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type}")
        return _ALLOWED_OPERATORS[op_type](
            _eval_node(node.left), _eval_node(node.right)
        )
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type}")
        return _ALLOWED_OPERATORS[op_type](_eval_node(node.operand))
    raise ValueError("Unsupported expression")


def calculator(expression: str) -> tuple[bool, str]:
    """Evaluate a basic arithmetic expression safely, e.g. '27 * 43'."""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return True, str(result)
    except Exception as e:
        return False, f"Couldn't evaluate '{expression}': {e}"


def current_time(_: str = "") -> tuple[bool, str]:
    """Return the current date and time."""
    return True, datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")


TOOLS = {
    # --- 4.1 ---
    "calculator": {
        "function": calculator,
        "description": "Evaluate arithmetic expressions like '27 * 43' or '(12+8)/4'. Input: a math expression string.",
        "risk": "read_only",
    },
    "current_time": {
        "function": current_time,
        "description": "Get the current date and time. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "recent_actions": {
        "function": recent_actions,
        "description": "Review the most recent actions JARVIS has actually taken, from the audit log. Use this when asked what you've done recently/today. Input: how many to show, e.g. '10' (optional).",
        "risk": "read_only",
    },
    "get_weather": {
        "function": get_weather,
        "description": "Get the real current weather/temperature for a place. Input: a city or place name, e.g. 'Thane, India'.",
        "risk": "read_only",
    },
    # --- 4.2B: system ---
    "open_application": {
        "function": open_application,
        "description": "Open a Windows application like notepad, calculator, paint, explorer, cmd, task manager, control panel, chrome, edge, firefox, or spotify. Input: the app name.",
        "risk": "low_risk",
    },
    "list_known_apps": {
        "function": list_known_apps,
        "description": "List every app name JARVIS currently knows how to open. Use this when asked what apps you can open, not open_application. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "close_application": {
        "function": close_application,
        "description": "Close a running application like notepad, chrome, or spotify. Input: the app name.",
        "risk": "destructive",
    },
    "set_volume": {
        "function": set_volume,
        "description": "Set the system volume to a specific percentage. Input: a number 0-100, e.g. '30'.",
        "risk": "low_risk",
    },
    "take_screenshot": {
        "function": take_screenshot,
        "description": "Take a screenshot of the screen and save it. Input: ignored, pass empty string.",
        "risk": "low_risk",
    },
    "get_running_applications": {
        "function": get_running_applications,
        "description": "List which known apps are currently running. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "switch_application": {
        "function": switch_application,
        "description": "Bring a running application's window to the foreground. Input: the app name.",
        "risk": "low_risk",
    },
    "mute": {
        "function": mute,
        "description": "Mute or unmute system audio (toggles). Input: ignored, pass empty string.",
        "risk": "low_risk",
    },
    "get_volume": {
        "function": get_volume,
        "description": "Get the current system volume level. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "system_info": {
        "function": system_info,
        "description": "Get basic system info: OS, CPU count, total RAM. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "cpu_usage": {
        "function": cpu_usage,
        "description": "Get current CPU usage percentage. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "memory_usage": {
        "function": memory_usage,
        "description": "Get current RAM usage. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "battery_status": {
        "function": battery_status,
        "description": "Get battery percentage and charging status, if this device has a battery. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "get_disk_usage": {
        "function": get_disk_usage,
        "description": "Get free/used/total disk space on the main drive. Use this for questions about storage space. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    # --- 4.2C: filesystem ---
    "search_files": {
        "function": search_files,
        "description": "Search for files by name under the user's home directory. Input: a filename or partial filename to search for.",
        "risk": "read_only",
    },
    "read_file": {
        "function": read_file,
        "description": "Read the contents of a text file. Input: the full file path.",
        "risk": "read_only",
    },
    "create_file": {
        "function": create_file,
        "description": 'Create a new file. Input: JSON string like \'{"path": "C:/path/file.txt", "content": "text"}\'.',
        "risk": "destructive",
    },
    "write_file": {
        "function": write_file,
        "description": 'Overwrite an existing file. Input: JSON string like \'{"path": "C:/path/file.txt", "content": "text"}\'.',
        "risk": "destructive",
    },
    "move_file": {
        "function": move_file,
        "description": 'Move a file. Input: JSON string like \'{"source": "path/a.txt", "destination": "path/b.txt"}\'.',
        "risk": "destructive",
    },
    "copy_file": {
        "function": copy_file,
        "description": 'Copy a file. Input: JSON string like \'{"source": "path/a.txt", "destination": "path/b.txt"}\'.',
        "risk": "destructive",
    },
    "delete_file": {
        "function": delete_file,
        "description": "Delete a file. Input: the full file path.",
        "risk": "destructive",
    },
    "rename_file": {
        "function": rename_file,
        "description": 'Rename a file. Input: JSON string like \'{"path": "C:/path/old.txt", "new_name": "new.txt"}\'.',
        "risk": "destructive",
    },
    # --- 4.2D: browser ---
    "open_browser": {
        "function": open_browser,
        "description": "Open a visible Chromium browser window. Input: ignored, pass empty string.",
        "risk": "low_risk",
    },
    "navigate": {
        "function": navigate,
        "description": "Navigate the open browser to a URL. Input: a URL, e.g. 'instagram.com'.",
        "risk": "low_risk",
    },
    "click": {
        "function": click,
        "description": "Click a button or link on the current page by its visible text or purpose. Input: the text/purpose to click, e.g. 'search'.",
        "risk": "low_risk",
    },
    "type_text": {
        "function": type_text,
        "description": 'Type into a form field on the current page. Input: JSON like \'{"selector": "placeholder text", "text": "what to type"}\'.',
        "risk": "low_risk",
    },
    "read_page": {
        "function": read_page,
        "description": "Read the visible text of the current browser page. Input: ignored, pass empty string.",
        "risk": "read_only",
    },
    "close_browser": {
        "function": close_browser,
        "description": "Close the browser window. Input: ignored, pass empty string.",
        "risk": "destructive",
    },
    # --- lightweight web ---
    "web_search": {
        "function": web_search,
        "description": "Search the web for real results (no browser needed). Input: a search query.",
        "risk": "read_only",
    },
    "fetch_url": {
        "function": fetch_url,
        "description": "Fetch a webpage's visible text content (no browser needed, static pages only). Input: a URL.",
        "risk": "read_only",
    },
    # --- memory ---
    "remember_fact": {
        "function": remember_fact,
        "description": "Explicitly save a fact/preference the user asks you to remember (e.g. 'remember my favorite song is X'). Input: the concrete fact in plain text, resolved from context if the user used a pronoun like 'it'.",
        "risk": "low_risk",
    },
}


def tool_descriptions() -> str:
    return "\n".join(f"- {name}: {meta['description']}" for name, meta in TOOLS.items())


def _log_tool_call(name: str, tool_input: str, ok: bool, message: str) -> None:
    """Append a structured record of every tool call to tool_calls.log."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tool": name,
        "input": tool_input,
        "ok": ok,
        "message": message[:300],  # keep the log readable, not a content dump
    }
    try:
        with open("tool_calls.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Logging must never break a live turn -- fail silently if disk write fails.
        pass


def run_tool(name: str, tool_input: str) -> dict:
    """
    Runs a tool and returns a structured result:
        {"ok": bool, "result": str, "risk": str}
    Every call is also appended to tool_calls.log for a real audit trail.
    """
    if name not in TOOLS:
        message = f"Unknown tool '{name}'."
        _log_tool_call(name, tool_input, False, message)
        return {"ok": False, "result": message, "risk": "unknown"}

    meta = TOOLS[name]
    start = time.time()
    try:
        ok, message = meta["function"](tool_input)
    except Exception as e:
        # A tool crashing outright (unhandled exception) is still a failure,
        # not something to let bubble up and take the whole turn down with it.
        ok, message = False, f"Tool '{name}' crashed unexpectedly: {e}"

    elapsed = time.time() - start
    if elapsed > 15:
        print(f"[TOOL SLOW] {name} took {elapsed:.1f}s")

    _log_tool_call(name, tool_input, ok, message)
    return {"ok": ok, "result": message, "risk": meta.get("risk", "unknown")}