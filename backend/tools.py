"""
JARVIS Tool registry.

Phase 4.1 (unchanged): calculator, current_time.
Phase 4.2B: open_application            -> tool/system.py
Phase 4.2C: filesystem tools            -> tool/filesystem.py
Phase 4.2D: browser tools               -> tool/browser.py
Phase 4.2  : lightweight web tools      -> tool/web.py

This file only registers name -> (function, description). Implementations
live under tool/. The agent never imports tool/* directly -- it only ever
sees TOOLS via tool_descriptions() and run_tool().
"""

import ast
import operator
from datetime import datetime

from tool.system import open_application, list_known_apps
from tool.filesystem import search_files, read_file, create_file, write_file, move_file, copy_file
from tool.browser import open_browser, navigate, click, type_text, read_page, close_browser
from tool.web import web_search, fetch_url

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


def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression safely, e.g. '27 * 43'."""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except Exception as e:
        return f"Error evaluating expression: {e}"


def current_time(_: str = "") -> str:
    """Return the current date and time."""
    return datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")


TOOLS = {
    # --- 4.1 ---
    "calculator": {
        "function": calculator,
        "description": "Evaluate arithmetic expressions like '27 * 43' or '(12+8)/4'. Input: a math expression string.",
    },
    "current_time": {
        "function": current_time,
        "description": "Get the current date and time. Input: ignored, pass empty string.",
    },
    # --- 4.2B: system ---
    "open_application": {
        "function": open_application,
        "description": "Open a Windows application like notepad, calculator, paint, explorer, cmd, task manager, or control panel. Input: the app name.",
    },
    "list_known_apps": {
        "function": list_known_apps,
        "description": "List every app name JARVIS currently knows how to open. Use this when asked what apps you can open, not open_application. Input: ignored, pass empty string.",
    },
    # --- 4.2C: filesystem ---
    "search_files": {
        "function": search_files,
        "description": "Search for files by name under the user's home directory. Input: a filename or partial filename to search for.",
    },
    "read_file": {
        "function": read_file,
        "description": "Read the contents of a text file. Input: the full file path.",
    },
    "create_file": {
        "function": create_file,
        "description": 'Create a new file. Input: JSON string like \'{"path": "C:/path/file.txt", "content": "text"}\'.',
    },
    "write_file": {
        "function": write_file,
        "description": 'Overwrite an existing file. Input: JSON string like \'{"path": "C:/path/file.txt", "content": "text"}\'.',
    },
    "move_file": {
        "function": move_file,
        "description": 'Move a file. Input: JSON string like \'{"source": "path/a.txt", "destination": "path/b.txt"}\'.',
    },
    "copy_file": {
        "function": copy_file,
        "description": 'Copy a file. Input: JSON string like \'{"source": "path/a.txt", "destination": "path/b.txt"}\'.',
    },
    # --- 4.2D: browser ---
    "open_browser": {
        "function": open_browser,
        "description": "Open a visible Chromium browser window. Input: ignored, pass empty string.",
    },
    "navigate": {
        "function": navigate,
        "description": "Navigate the open browser to a URL. Input: a URL, e.g. 'instagram.com'.",
    },
    "click": {
        "function": click,
        "description": "Click a button or link on the current page by its visible text. Input: the text to click.",
    },
    "type_text": {
        "function": type_text,
        "description": 'Type into a form field on the current page. Input: JSON like \'{"selector": "placeholder text", "text": "what to type"}\'.',
    },
    "read_page": {
        "function": read_page,
        "description": "Read the visible text of the current browser page. Input: ignored, pass empty string.",
    },
    "close_browser": {
        "function": close_browser,
        "description": "Close the browser window. Input: ignored, pass empty string.",
    },
    # --- 4.2: lightweight web ---
    "web_search": {
        "function": web_search,
        "description": "Search the web for a quick factual summary (no browser needed). Input: a search query.",
    },
    "fetch_url": {
        "function": fetch_url,
        "description": "Fetch a webpage's visible text content (no browser needed, static pages only). Input: a URL.",
    },
}


def tool_descriptions() -> str:
    return "\n".join(f"- {name}: {meta['description']}" for name, meta in TOOLS.items())


def run_tool(name: str, tool_input: str) -> str:
    if name not in TOOLS:
        return f"Error: unknown tool '{name}'"
    return TOOLS[name]["function"](tool_input)