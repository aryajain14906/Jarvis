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

from tools import tool_descriptions, run_tool, TOOLS
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
- ANY request to go to, open, visit, or navigate to a website or URL (e.g. "go to wikipedia.org",
  "go to a page about chess") MUST use the navigate tool with the actual URL/site as input. NEVER
  answer a navigation request with "the browser is open" or similar -- if you didn't call navigate,
  nothing actually moved, and claiming it worked is a fabrication.
- If the destination isn't a real, known URL (e.g. "go to a page that relates to chess" with no
  specific site named), use navigate with your best real guess of a URL (e.g. "wikipedia.org/wiki/Chess")
  rather than answering directly -- an imperfect real navigation beats a fabricated claim.
- NEVER claim you performed an action (playing a song, pausing/skipping music, adjusting volume,
  posting something, sending a message, clicking something, typing something, navigating somewhere)
  unless one of the tools listed above actually does that. If asked to do something with no matching
  tool, say plainly that you can't do that yet -- do not invent a plausible-sounding result. Making
  something up is worse than admitting a limitation.
- If the user asks you to "remember" something, use the remember_fact tool. If they refer to it
  vaguely (e.g. "remember it", "remember that"), resolve what "it" means from the recent conversation
  provided above and pass the concrete fact as input -- never invent a fake conversation log or
  fabricate content that wasn't actually said.
- NEVER reproduce song lyrics, poems, or copyrighted text, even if asked to "complete the lyrics" or
  the user starts singing them. Say you can't reproduce lyrics, and offer to name the song/artist
  instead if you recognize it.
- search_files is ONLY for finding local files on disk by name (e.g. "find my resume"). If the user
  says "search X in it/in the browser/on Chrome" (referring to the open browser), that means searching
  the web -- use the navigate tool with input like "google.com/search?q=X", never search_files.
- read_page is for anything on the currently open browser page (e.g. "read the page", "what does it
  say", "read the content of wikipedia.org that you opened"). read_file is ONLY for a specific named
  file/path on disk. If it's unclear which one is meant and a browser was recently opened/navigated,
  prefer read_page over guessing a fake file path.
- For greetings, opinions, jokes, or general knowledge you're confident about, answer directly.
- Never invent tool names that aren't listed above.
- Always respond with valid JSON and nothing else. Never use any action value other than exactly
  "tool" or "answer" -- never put a tool name directly as the action.

Examples:
User: "what is 7 * 43"
{{"action": "tool", "tool": "calculator", "input": "7 * 43"}}

User: "how are you"
{{"action": "answer", "content": "I am functioning within optimal parameters, thank you for inquiring."}}

User: "open chrome"
{{"action": "tool", "tool": "open_application", "input": "chrome"}}

User: "which apps can you open"
{{"action": "tool", "tool": "list_known_apps", "input": ""}}

User: "close spotify"
{{"action": "tool", "tool": "close_application", "input": "spotify"}}

User: "set volume to 30 percent"
{{"action": "tool", "tool": "set_volume", "input": "30"}}

User: "take a screenshot"
{{"action": "tool", "tool": "take_screenshot", "input": ""}}

User: "go to wikipedia.org"
{{"action": "tool", "tool": "navigate", "input": "wikipedia.org"}}

User: "go to a page about chess"
{{"action": "tool", "tool": "navigate", "input": "wikipedia.org/wiki/Chess"}}

User: "click English"
{{"action": "tool", "tool": "click", "input": "English"}}

User: "type hello into the search box"
{{"action": "tool", "tool": "type_text", "input": "{{\\"selector\\": \\"search\\", \\"text\\": \\"hello\\"}}"}}

User: "remember that my favorite song is Where We Are by One Direction"
{{"action": "tool", "tool": "remember_fact", "input": "favorite song is Where We Are by One Direction"}}

User: "search Claude in it"
{{"action": "tool", "tool": "navigate", "input": "google.com/search?q=Claude"}}

User: "read the page"
{{"action": "tool", "tool": "read_page", "input": ""}}

User: "read the content of wikipedia.org that you opened"
{{"action": "tool", "tool": "read_page", "input": ""}}

User: "find my resume"
{{"action": "tool", "tool": "search_files", "input": "resume"}}
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


_LYRIC_REQUEST_PATTERN = re.compile(
    r"\blyrics?\b|\bsing (?:me|the song|it)\b|\bcontinue (?:the )?(?:rest of )?(?:the )?(?:song|verse)\b",
    re.IGNORECASE,
)

_ID_ONLY_SYSTEM = """You identify songs by title and artist from what the user describes or quotes.
You must NEVER output any actual lyric lines, even a single line, even paraphrased closely, even
if the user quotes lyrics at you first. If you recognize the song, reply with only the title and
artist, e.g. "That's 'The Night We Met' by Lord Huron." If you don't recognize it, say so honestly.
Do not include any lyric text in your reply under any circumstances."""


def _handle_lyric_request(question: str, context: str = "") -> str:
    """
    Deterministic guard: lyric-completion requests never reach the normal
    decide/answer path, because prompt rules alone weren't reliably stopping
    a small local model from reproducing (and sometimes misattributing)
    copyrighted lyrics. This bypasses that risk entirely by routing straight
    to an identify-only call.
    """
    messages = [{"role": "user", "content": question}]
    reply = _call_ollama(_ID_ONLY_SYSTEM, messages).strip()
    return (
        f"{reply} I can't reproduce the actual lyrics, but I'm happy to tell you more "
        "about the song, or open it for you if you'd like."
    )


def run_agent(question: str, context: str = "") -> str:
    """
    Runs one decide -> (tool) -> answer cycle. Returns the final spoken answer.
    Drop-in replacement for ask_llm(question, context).
    """
    if _LYRIC_REQUEST_PATTERN.search(question):
        print("[AGENT] Lyric request detected -- routing to identify-only guard.")
        return _handle_lyric_request(question, context)

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
        # Model ignored the JSON format entirely -- don't leak raw JSON/text
        # to the user, say so honestly instead.
        print(f"[AGENT] Failed to parse decision JSON: {decision_raw}")
        return "Sorry, I got a bit confused processing that -- could you try rephrasing?"

    action = decision.get("action")

    # Defensive normalization: sometimes the model puts a tool name directly
    # as the action (e.g. {"action": "type_text", "content": {...}}) instead
    # of following the {"action": "tool", "tool": ..., "input": ...} shape.
    # If that happens, recover it as a real tool call instead of falling
    # through and leaking raw JSON to the user.
    if action in TOOLS:
        tool_name = action
        raw_input = decision.get("input", decision.get("content", ""))
        tool_input = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
        print(f"[AGENT] Normalized malformed action '{action}' into a tool call.")
        action = "tool"
        decision = {"action": "tool", "tool": tool_name, "input": tool_input}

    if action == "tool":
        tool_name = decision.get("tool", "")
        tool_input = decision.get("input", "")
        print(f"[TOOL] {tool_name}({tool_input!r})")
        tool_result = run_tool(tool_name, tool_input)
        print(f"[TOOL RESULT] {tool_result}")

        # Deterministic override: open_application on a media app only launches
        # the process -- it never searches/queues/plays a specific track. Prompt
        # rules alone weren't reliably stopping the model from claiming playback
        # anyway, so handle this case directly instead of trusting the phrasing
        # call to remember the rule.
        media_apps = {"spotify"}
        if tool_name == "open_application" and tool_input.strip().lower() in media_apps:
            return (
                f"{tool_result} I can open {tool_input.strip()}, but I don't have a way to "
                "search for or play a specific track yet -- you'll need to do that part yourself."
            )

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
                "content": (
                    f"Tool used: {tool_name}\n"
                    f"Actual tool result: {tool_result!r}\n\n"
                    "Phrase this result as a natural spoken answer. Only state information that is "
                    "literally present in the tool result above -- do not add any extra facts, "
                    "numbers, names, or details that aren't in it. If the tool result is empty, vague, "
                    "or doesn't really answer the question, say that plainly instead of filling in "
                    "plausible-sounding content of your own."
                ),
            },
        ]
        return _call_ollama(final_system, final_messages).strip()

    if action == "answer":
        return decision.get("content", "").strip()

    print(f"[AGENT] Unrecognized action in decision: {decision}")
    return "Sorry, I wasn't sure how to handle that -- could you try rephrasing?"


if __name__ == "__main__":
    for q in ["What is 27 × 43?", "Tell me a joke."]:
        print(f"\nUSER: {q}")
        print(f"JARVIS: {run_agent(q)}")