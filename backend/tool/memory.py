"""
tool/memory.py
================
Lets the agent explicitly store a fact on request ("remember this for me"),
on top of the automatic background extraction that already happens in
memory.py's record_turn(). Without this, the model has nothing real to call
when asked to "remember" something -- which is exactly what caused it to
invent a nonexistent "remember" action and hallucinate fake conversation logs.
"""

from memory import get_shared_memory_manager


def remember_fact(content: str) -> str:
    """Explicitly store a fact/preference the user wants remembered. Input: the fact in plain text."""
    manager = get_shared_memory_manager()
    if manager is None:
        return "Memory isn't wired up yet -- I can't save that right now."
    fact = content.strip()
    if not fact:
        return "I don't have anything concrete to remember from that -- could you say it again plainly?"
    manager.long_term.store_fact(fact)
    return f"Got it, I'll remember: {fact}"