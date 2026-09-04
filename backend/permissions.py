"""
permissions.py
================
Phase 4.2F -- safety/permission layer.

Lets you control which tools require confirmation or are denied outright,
via a simple editable JSON file (permissions.json) that sits next to
jarvis_memory.db. This is separate from the hardcoded path blocklist inside
tool/filesystem.py -- this layer works across ALL tools by risk tier, not
just filesystem paths.

Edit permissions.json directly to customize, e.g.:
    {
      "denied_tools": ["close_application"],
      "confirm_risk_levels": ["destructive"]
    }
"""

import json
import os

_CONFIG_PATH = "permissions.json"

_DEFAULT_CONFIG = {
    # Tool names JARVIS may never call, regardless of what the model decides.
    "denied_tools": [],
    # Which risk tiers (see the "risk" field in tools.py's TOOLS dict) require
    # an explicit yes/no from the user before actually running.
    "confirm_risk_levels": ["destructive"],
}


def load_permissions() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        save_permissions(_DEFAULT_CONFIG)
        return dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception:
        # A corrupted config should never take the whole assistant down --
        # fall back to safe defaults instead.
        return dict(_DEFAULT_CONFIG)


def save_permissions(config: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def is_denied(tool_name: str) -> bool:
    return tool_name in load_permissions().get("denied_tools", [])


def requires_confirmation(risk: str) -> bool:
    return risk in load_permissions().get("confirm_risk_levels", [])