"""
agent.py
========
JARVIS Phase 4.1 — Agent foundation.

Flow:
  question -> ask Ollama "tool or answer?" (as JSON)
           -> if tool: run it locally, ask Ollama to phrase the final reply
              (using JARVIS's real persona from llm.py, so it still sounds
              like JARVIS and not a generic bot)
           -> if answer: return content directly

Drop-in replacement for ask_llm() in app.py:
    from agent import run_agent
    answer = run_agent(question, context=context)
"""

import json
import re
import requests

from tools import tool_descriptions, run_tool
from llm import OLLAMA_URL, MODEL_NAME, SYSTEM_PROMPT

AGENT_SYSTEM_PROMPT = f"""You are JARVIS's decision-making core.
For every user request, decide whether you need a tool or can answer directly.

Available tools:
{tool_descriptions()}

Respond with ONLY a JSON object, no extra text, no markdown fences, in one of these two forms:

If you need a tool:
{{"action": "tool", "tool": "<tool_name>", "input": "<tool_input>"}}

If you can answer directly:
{{"action": "answer", "content": "<your answer>"}}

Rules:
- Only use a tool when it is actually needed to answer accurately (math, current date/time, etc).
- ANY arithmetic, no matter how simple (e.g. "7 times 43", "12 plus 8"), MUST use the calculator
  tool. Never compute math yourself, even if it looks easy -- you are not reliable at mental math
  and a wrong number is worse than a tool call.
- NEVER claim you performed an action (playing a song, pausing/skipping music, adjusting volume,
  posting something, sending a message, etc.) unless one of the tools listed above actually does
  that. If asked to do something with no matching tool, say plainly that you can't do that yet --
  do not invent a plausible-sounding result (e.g. a fake song title). Making something up is worse
  than admitting a limitation.
- For greetings, opinions, jokes, or general knowledge you're confident about, answer directly.
- Never invent tool names that aren't listed above.
- Always respond with valid JSON and nothing else.

Examples:
User: "what is 7 * 43"
{{"action": "tool", "tool": "calculator", "input": "7 * 43"}}

User: "how are you"
{{"action": "answer", "content": "I am functioning within optimal parameters, thank you for inquiring."}}

User: "open chrome"
{{"action": "tool", "tool": "open_application", "input": "chrome"}}

User: "which apps can you open"
{{"action": "tool", "tool": "list_known_apps", "input": ""}}
"""


def _call_ollama(system_content: str, messages: list) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": system_content}] + messages,
        "stream": False,
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]
    except requests.exceptions.ConnectionError:
        return (
            "I can't reach Ollama right now, sir. Make sure it's running "
            "-- try `ollama serve` in a terminal."
        )
    except Exception as e:
        return f"Error: {str(e)}"


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {text}")
    raw = match.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Model likely put unescaped quotes inside a string value (e.g. a song
        # title in quotes). Fall back to pulling action/tool/input/content out
        # with regex instead of failing the whole turn.
        action_m = re.search(r'"action"\s*:\s*"(\w+)"', raw)
        if not action_m:
            raise ValueError(f"Could not parse action from: {raw}")
        result = {"action": action_m.group(1)}
        tool_m = re.search(r'"tool"\s*:\s*"([^"]*)"', raw)
        input_m = re.search(r'"input"\s*:\s*"(.*?)"\s*}\s*$', raw, re.DOTALL)
        content_m = re.search(r'"content"\s*:\s*"(.*)"\s*}\s*$', raw, re.DOTALL)
        if tool_m:
            result["tool"] = tool_m.group(1)
        if input_m:
            result["input"] = input_m.group(1)
        if content_m:
            result["content"] = content_m.group(1)
        return result


def run_agent(question: str, context: str = "") -> str:
    """
    Runs one decide -> (tool) -> answer cycle. Returns the final spoken answer.
    Drop-in replacement for ask_llm(question, context).
    """
    decision_system = AGENT_SYSTEM_PROMPT
    if context:
        decision_system += (
            "\n\nRecent conversation and memory (for your awareness -- referencing "
            f"this does NOT require a tool call):\n{context}"
        )

    decision_raw = _call_ollama(decision_system, [{"role": "user", "content": question}])
    print(f"[AGENT DECISION RAW] {decision_raw}")

    try:
        decision = _extract_json(decision_raw)
    except ValueError:
        # Model ignored the JSON format -- fall back to a normal JARVIS answer.
        return decision_raw

    action = decision.get("action")

    if action == "tool":
        tool_name = decision.get("tool", "")
        tool_input = decision.get("input", "")
        print(f"[TOOL] {tool_name}({tool_input!r})")
        tool_result = run_tool(tool_name, tool_input)
        print(f"[TOOL RESULT] {tool_result}")

        # Phrase the final answer in JARVIS's real voice (same persona as llm.py),
        # with memory context included just like ask_llm normally would.
        final_system = SYSTEM_PROMPT
        if context:
            final_system += (
                "\n\nRelevant memory (use naturally if relevant, don't mention "
                f"that you're 'recalling' anything):\n{context}"
            )

        final_messages = [
            {"role": "user", "content": question},
            {
                "role": "user",
                "content": f"(Tool result -- {tool_name}: {tool_result}). "
                           f"Give the final spoken answer using this result, naturally.",
            },
        ]
        return _call_ollama(final_system, final_messages).strip()

    if action == "answer":
        return decision.get("content", "").strip()

    return decision_raw


if __name__ == "__main__":
    for q in ["What is 27 × 43?", "Tell me a joke."]:
        print(f"\nUSER: {q}")
        print(f"JARVIS: {run_agent(q)}")