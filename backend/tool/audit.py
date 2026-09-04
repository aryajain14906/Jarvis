"""
tool/audit.py
==============
Phase 4.2F -- lets the user ask what JARVIS has actually done recently,
reading straight from tool_calls.log (written by tools.py's run_tool in 4.2E).
This is the "reviewable log" half of the safety story: 4.2E records every
call, this exposes it back to the user as a real answer instead of only
being visible in terminal scrollback.
"""

import json
import os


def recent_actions(count_str: str = "") -> tuple[bool, str]:
    """List the most recent tool actions JARVIS has taken. Input: optional number, defaults to 10."""
    count = 10
    if count_str.strip().isdigit():
        count = int(count_str.strip())

    if not os.path.exists("tool_calls.log"):
        return True, "No actions have been logged yet."

    try:
        with open("tool_calls.log", "r", encoding="utf-8") as f:
            lines = [line for line in f.readlines() if line.strip()]
        entries = [json.loads(line) for line in lines[-count:]]
        if not entries:
            return True, "No actions have been logged yet."

        summary = "; ".join(
            f"{e['tool']}({e['input']!r}) -> {'ok' if e['ok'] else 'failed'}"
            for e in entries
        )
        return True, summary
    except Exception as e:
        return False, f"Error reading action log: {e}"