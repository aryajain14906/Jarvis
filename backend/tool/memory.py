"""
tool/memory.py
================
Lets the agent explicitly store a fact on request ("remember this for me"),
on top of the automatic background extraction that already happens in
memory.py's record_turn().

Phase 4.2E update: returns (ok: bool, message: str) instead of a bare string.
"""

from memory import get_shared_memory_manager


def remember_fact(content: str) -> tuple[bool, str]:
    """Explicitly store a fact/preference the user wants remembered. Input: the fact in plain text."""
    manager = get_shared_memory_manager()
    if manager is None:
        return False, "Memory isn't wired up yet -- I can't save that right now."
    fact = content.strip()
    if not fact:
        return False, "There's nothing concrete to remember from that -- could you say it again plainly?"
    manager.long_term.store_fact(fact)
    return True, f"Got it, I'll remember: {fact}"