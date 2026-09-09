"""
agent.py
========
JARVIS Agent -- decision core, through Phase 4.3 (multi-step execution).

Flow per turn:
  1. If a destructive tool call is awaiting yes/no confirmation, resolve
     that deterministically before anything else.
  2. If the message looks like a lyric request, route to a locked-down
     identify-only call (never the normal decide/answer path).
  3. Otherwise: loop up to MAX_STEPS times -- ask Ollama "tool or answer?",
     if tool: run it (unless denied/needs confirmation), feed the real
     result back, and ask again ("re-plan"). Once it says "answer" (or
     MAX_STEPS is hit), phrase one final reply from everything gathered.

Drop-in replacement for ask_llm() in app.py:
    from agent import run_agent
    answer = run_agent(question, context=context)
"""

import json
import re
import requests

from tools import tool_descriptions, run_tool, TOOLS
from llm import OLLAMA_URL, MODEL_NAME, SYSTEM_PROMPT
from permissions import is_denied, requires_confirmation

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
- If a tool result says something is missing/not installed/unavailable, do NOT try to fix it yourself.
  There is no "pip" tool, no "install" tool, and searching local files for documentation is never the
  right response to a missing dependency. Just report the honest failure in your final answer (e.g.
  "volume control isn't set up on this device yet") and stop -- do not spend further steps inventing
  ways to repair your own environment.
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
- see_screen and read_screen_text are DIFFERENT tools for different needs. see_screen gives a general,
  approximate description (which app, rough layout) -- use it for "what's on my screen", "what app am I
  using". It is NOT reliable for exact wording. read_screen_text uses real OCR and gives exact text --
  use it for "what does the error say", "read this message", "what does that button say", anything where
  precise wording matters. When phrasing a see_screen result, hedge it ("it looks like...", "this appears
  to be...") rather than stating it as confirmed fact -- vision descriptions can be confidently wrong
  about specific details even when the general gist is right.
- click_on_screen is EXPERIMENTAL and ONLY for desktop application UI with no other way to interact.
  If a browser is open and the target is on a webpage, use the browser's own "click" tool instead --
  it's far more reliable than guessing screen coordinates. Only use click_on_screen for native desktop
  app buttons/elements that a browser tool can't reach.
- Current weather/temperature for a place MUST use the get_weather tool -- you cannot know live weather from
  training knowledge, no matter how confident you feel. Never estimate or guess a temperature yourself.
- Dates of festivals/holidays that follow a lunar or lunisolar calendar (e.g. Navratri, Diwali, Janmashtami,
  Eid) shift every year and can't be reliably recalled from memory -- use web_search for these instead of
  guessing or hedging with vague date ranges.
- NEVER claim a specific song is "currently playing" or "now playing" unless a real playback tool actually
  started it. If asked to play/what's playing and there's no real playback capability, say so honestly --
  do not name a song title, even if the user has a stored favorite song in memory. Mentioning a stored
  favorite as a suggestion ("I don't see a way to play music, but I know you like X") is fine; claiming it's
  actively playing is not.
- For greetings, opinions, jokes, or general knowledge you're confident about, answer directly.
- Requests for reviews, opinions, discussion, or general facts about movies, books, people, or topics
  should usually be answered DIRECTLY from your own knowledge -- do not force a web_search call for
  these. Only use web_search when the user explicitly needs current/live/"latest"/"today's" information
  that your own knowledge can't cover, or when you genuinely don't recognize something specific (e.g.
  an obscure title) and want to check.
- When building input for ANY tool, use only the topic/words from the user's CURRENT message. Do not
  blend in unrelated topics, names, or subjects from earlier turns in the conversation above, even if
  they're mentioned in the recent-conversation context -- only carry earlier context forward if the
  current message clearly continues the same topic (e.g. using "it", "that", "the same one").
- You are allowed to use MORE THAN ONE tool per request if it genuinely needs it (e.g. "open calculator
  and compute 12*7" needs open_application THEN calculator). Do ONE tool per response -- you'll be shown
  the real result and asked what to do next, for up to a few steps. Never claim a later part of a
  multi-part request is done just because an earlier part succeeded; only the tool result you're
  actually shown is real.
- open_application("chrome"/"edge"/"firefox") and navigate() control TWO COMPLETELY DIFFERENT browsers.
  open_application opens the user's own everyday browser (their real bookmarks/logins) -- use it ONLY
  if the user explicitly wants their own browser opened, with nothing further to do in it. navigate()
  controls a separate, dedicated automation browser and ALREADY opens it automatically -- never call
  open_application first when the goal is to visit/search a website. "Go to YouTube" or "open Chrome and
  search for X" should go STRAIGHT to navigate, not open_application.
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

User: "open calculator and compute 12 times 7"
{{"action": "tool", "tool": "open_application", "input": "calculator"}}
(-- then, after seeing that result, the NEXT response should be the calculator tool call itself)

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

User: "open chrome and go to youtube"
{{"action": "tool", "tool": "navigate", "input": "youtube.com"}}
(-- navigate alone is correct here; it opens its own browser, open_application is not needed)

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

User: "what have you actually done today"
{{"action": "tool", "tool": "recent_actions", "input": ""}}

User: "what's the current temperature in Thane"
{{"action": "tool", "tool": "get_weather", "input": "Thane, India"}}

User: "when is the next festival in India"
{{"action": "tool", "tool": "web_search", "input": "next Hindu festival India date"}}

User: "what apps are currently running"
{{"action": "tool", "tool": "get_running_applications", "input": ""}}

User: "switch to notepad"
{{"action": "tool", "tool": "switch_application", "input": "notepad"}}

User: "mute the volume"
{{"action": "tool", "tool": "mute", "input": ""}}

User: "what's the current volume"
{{"action": "tool", "tool": "get_volume", "input": ""}}

User: "what's my cpu usage"
{{"action": "tool", "tool": "cpu_usage", "input": ""}}

User: "how much ram am i using"
{{"action": "tool", "tool": "memory_usage", "input": ""}}

User: "what's my battery percentage"
{{"action": "tool", "tool": "battery_status", "input": ""}}

User: "what's on my screen"
{{"action": "tool", "tool": "see_screen", "input": ""}}

User: "what app am I currently using"
{{"action": "tool", "tool": "see_screen", "input": "What application is currently the main focus on screen?"}}

User: "what does the error message say"
{{"action": "tool", "tool": "read_screen_text", "input": ""}}

User: "read the text on my screen"
{{"action": "tool", "tool": "read_screen_text", "input": ""}}

User: "delete the file test.txt on my desktop"
{{"action": "tool", "tool": "delete_file", "input": "C:/Users/jains/Desktop/test.txt"}}

User: "rename report.txt to report_final.txt"
{{"action": "tool", "tool": "rename_file", "input": "{{\\"path\\": \\"report.txt\\", \\"new_name\\": \\"report_final.txt\\"}}"}}
"""

_CONFIRM_YES_PATTERN = re.compile(r"^\s*(yes|yeah|yep|sure|go ahead|do it|confirm|okay|ok)\b", re.IGNORECASE)
_CONFIRM_NO_PATTERN = re.compile(r"^\s*(no|nope|don'?t|cancel|stop|nevermind|never mind)\b", re.IGNORECASE)

# Holds a single destructive tool call awaiting yes/no confirmation.
# Single-user, single-session assistant, so module-level state is fine here
# -- mirrors the same pattern used for the shared memory manager.
_pending_confirmation = None

_LYRIC_REQUEST_PATTERN = re.compile(
    r"\blyrics?\b|\bsing (?:me|the song|it)\b|\bcontinue (?:the )?(?:rest of )?(?:the )?(?:song|verse)\b",
    re.IGNORECASE,
)

_ID_ONLY_SYSTEM = """You identify songs by title and artist from what the user describes or quotes.
You must NEVER output any actual lyric lines, even a single line, even paraphrased closely, even
if the user quotes lyrics at you first. If you recognize the song, reply with only the title and
artist, e.g. "That's 'The Night We Met' by Lord Huron." If you don't recognize it, say so honestly.
Do not include any lyric text in your reply under any circumstances."""


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


def _handle_lyric_request(question: str) -> str:
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


def _run_tool_step(tool_name: str, tool_input: str) -> tuple[str | None, dict | None]:
    """
    Executes one tool call for the multi-step loop. Returns either:
      (final_answer, None)      -- a deterministic override fired; stop the whole
                                    turn immediately and return this as the answer.
      (None, observation)       -- normal case; loop should feed `observation`
                                    back to the model and ask for the next step.
    """
    print(f"[TOOL] {tool_name}({tool_input!r})")
    tool_call = run_tool(tool_name, tool_input)
    tool_ok = tool_call["ok"]
    tool_result = tool_call["result"]
    tool_risk = tool_call["risk"]
    print(f"[TOOL RESULT] ok={tool_ok} risk={tool_risk} -- {tool_result}")

    # Deterministic override: open_application on a media app only launches the
    # process, never plays a specific track -- stop the whole turn here rather
    # than let a later step in the plan claim playback actually happened.
    media_apps = {"spotify"}
    if tool_name == "open_application" and tool_input.strip().lower() in media_apps:
        return (
            f"{tool_result} I can open {tool_input.strip()}, but I don't have a way to "
            "search for or play a specific track yet -- you'll need to do that part yourself."
        ), None

    return None, {"tool": tool_name, "input": tool_input, "ok": tool_ok, "result": tool_result, "risk": tool_risk}


def _finalize_answer(question: str, context: str, steps: list[dict]) -> str:
    """
    Phrases the final spoken answer once the loop is done, using JARVIS's real
    persona/voice. `steps` is the full list of {tool, input, ok, result, risk}
    observations gathered across however many tool calls actually ran.
    """
    final_system = SYSTEM_PROMPT
    if context:
        final_system += (
            "\n\nRelevant memory (use naturally if relevant, don't mention "
            f"that you're 'recalling' anything):\n{context}"
        )

    if not steps:
        # Shouldn't normally happen (means action=="answer" with zero tool
        # calls, which run_agent handles directly) -- safety net just in case.
        return _call_ollama(final_system, [{"role": "user", "content": question}]).strip()

    summary = "\n".join(
        f"- {s['tool']}({s['input']!r}) -> ok={s['ok']}: {s['result']!r}" for s in steps
    )

    # If the only/last thing tried was a failed read-only lookup, allow a
    # general-knowledge fallback (clearly labeled) instead of a flat "nothing
    # found" -- same reasoning as the single-tool case, extended to a plan.
    last = steps[-1]
    if not last["ok"] and last["risk"] == "read_only":
        instruction = (
            f"You tried these steps:\n{summary}\n\n"
            "The live lookup didn't return anything useful. You may answer using your own "
            "general knowledge instead, if you actually know something relevant -- say plainly "
            "that it's your general understanding, not confirmed live. If you genuinely don't "
            "know, say that honestly."
        )
    else:
        instruction = (
            f"You carried out these steps to answer the request:\n{summary}\n\n"
            "Phrase the final spoken answer now. Only state information that is literally "
            "present in the results above -- do not add extra facts, numbers, names, or claim "
            "any step succeeded that didn't. If the results don't fully answer the question, "
            "say that plainly instead of filling in plausible-sounding content of your own."
        )

    final_messages = [
        {"role": "user", "content": question},
        {"role": "user", "content": instruction},
    ]
    return _call_ollama(final_system, final_messages).strip()


def _execute_tool_call(tool_name: str, tool_input: str, question: str, context: str) -> str:
    """Runs a tool and phrases the result, applying every deterministic safety override."""
    print(f"[TOOL] {tool_name}({tool_input!r})")
    tool_call = run_tool(tool_name, tool_input)
    tool_ok = tool_call["ok"]
    tool_result = tool_call["result"]
    tool_risk = tool_call["risk"]
    print(f"[TOOL RESULT] ok={tool_ok} risk={tool_risk} -- {tool_result}")

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

    final_system = SYSTEM_PROMPT
    if context:
        final_system += (
            "\n\nRelevant memory (use naturally if relevant, don't mention "
            f"that you're 'recalling' anything):\n{context}"
        )

    # A read-only lookup tool (web_search, fetch_url) coming back empty is
    # different from an action tool failing -- there's nothing actually wrong,
    # the live source just didn't have anything. For these specific cases, let
    # the model fall back to its own general knowledge instead of just
    # relaying "no results found" -- but require it to say plainly that it's
    # not confirmed by a live search, so it stays honest about the source.
    if not tool_ok and tool_risk == "read_only":
        final_messages = [
            {"role": "user", "content": question},
            {
                "role": "user",
                "content": (
                    f"A live lookup ({tool_name}) didn't return anything useful: {tool_result!r}\n\n"
                    "You may answer the original question using your own general knowledge instead, "
                    "if you actually know something relevant. If you do, say plainly that this is "
                    "your general understanding and not confirmed by a live search. If you genuinely "
                    "don't know, say that honestly instead of guessing."
                ),
            },
        ]
        return _call_ollama(final_system, final_messages).strip()

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


MAX_STEPS = 4


def run_agent(question: str, context: str = "") -> str:
    """
    Runs the agent's decide -> act -> observe -> re-plan loop (Phase 4.3),
    up to MAX_STEPS tool calls, then phrases a final answer.
    Drop-in replacement for ask_llm(question, context).
    """
    global _pending_confirmation

    # Phase 4.2F: if a destructive tool call is awaiting confirmation, this
    # turn's ONLY job is to interpret yes/no -- deterministically, not via the
    # LLM, since past reliability issues showed it can't be trusted to parse
    # intent correctly under pressure. It never falls through to normal routing.
    if _pending_confirmation is not None:
        if _CONFIRM_YES_PATTERN.search(question):
            pending = _pending_confirmation
            _pending_confirmation = None
            print(f"[AGENT] Confirmed -- executing {pending['tool']}({pending['input']!r})")
            return _execute_tool_call(pending["tool"], pending["input"], pending["question"], context)
        if _CONFIRM_NO_PATTERN.search(question):
            _pending_confirmation = None
            return "Okay, I won't do that."
        return (
            f"I still need a yes or no -- should I go ahead with: {_pending_confirmation['description']}?"
        )

    if _LYRIC_REQUEST_PATTERN.search(question):
        print("[AGENT] Lyric request detected -- routing to identify-only guard.")
        return _handle_lyric_request(question)

    decision_system = AGENT_SYSTEM_PROMPT
    if context:
        decision_system += (
            "\n\nRecent conversation and memory (for your awareness -- referencing "
            f"this does NOT require a tool call):\n{context}"
        )

    conversation = [{"role": "user", "content": question}]
    steps_taken: list[dict] = []

    for step_num in range(1, MAX_STEPS + 1):
        decision_raw = _call_ollama(decision_system, conversation)
        print(f"[AGENT STEP {step_num}] DECISION RAW: {decision_raw}")

        try:
            decision = _extract_json(decision_raw)
        except ValueError:
            print(f"[AGENT] Failed to parse decision JSON: {decision_raw}")
            if steps_taken:
                # We have real progress from earlier steps -- don't throw it
                # away just because the LAST step's JSON was malformed.
                return _finalize_answer(question, context, steps_taken)
            return "Sorry, I got a bit confused processing that -- could you try rephrasing?"

        action = decision.get("action")

        # Defensive normalization: sometimes the model puts a tool name
        # directly as the action instead of the proper {"action": "tool", ...}
        # shape. Recover it as a real tool call instead of leaking raw JSON.
        if action in TOOLS:
            tool_name = action
            raw_input = decision.get("input", decision.get("content", ""))
            tool_input = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
            print(f"[AGENT] Normalized malformed action '{action}' into a tool call.")
            action = "tool"
            decision = {"action": "tool", "tool": tool_name, "input": tool_input}

        if action == "answer":
            if not steps_taken:
                # No tools used at all this turn -- unchanged from the
                # original single-shot behavior.
                return decision.get("content", "").strip()
            return _finalize_answer(question, context, steps_taken)

        if action != "tool":
            print(f"[AGENT] Unrecognized action in decision: {decision}")
            if steps_taken:
                return _finalize_answer(question, context, steps_taken)
            return "Sorry, I wasn't sure how to handle that -- could you try rephrasing?"

        tool_name = decision.get("tool", "")
        tool_input = decision.get("input", "")

        # Phase 4.2F: a hard deny wins regardless of what the LLM decided --
        # never even reaches a confirmation step.
        if is_denied(tool_name):
            print(f"[AGENT] '{tool_name}' is denied in permissions.json -- refusing without running it.")
            return f"I'm not allowed to use {tool_name} right now -- it's been disabled in permissions.json."

        risk = TOOLS.get(tool_name, {}).get("risk", "unknown")

        # Phase 4.2F: risk tiers listed in permissions.json's confirm_risk_levels
        # (destructive, by default) need an explicit yes before they run --
        # this stops the WHOLE turn, even mid-plan, until confirmed.
        if requires_confirmation(risk):
            description = f"use {tool_name} with input {tool_input!r}"
            _pending_confirmation = {"tool": tool_name, "input": tool_input, "question": question, "description": description}
            print(f"[AGENT] '{tool_name}' requires confirmation -- pausing before executing.")
            return f"I'm about to {description} -- this is a {risk} action. Should I go ahead? (yes/no)"

        short_circuit_answer, observation = _run_tool_step(tool_name, tool_input)
        if short_circuit_answer is not None:
            return short_circuit_answer

        steps_taken.append(observation)

        # Feed the observation back so the model can decide: another tool,
        # or ready to answer. This is the actual "re-plan" step of 4.3.
        conversation.append({"role": "assistant", "content": decision_raw})
        conversation.append({
            "role": "user",
            "content": (
                f"Tool result -- {observation['tool']}: {observation['result']!r} "
                f"(ok={observation['ok']}).\n\n"
                "Look at the ORIGINAL request again. If this result already fully answers it, "
                "respond with {\"action\": \"answer\", \"content\": \"...\"} RIGHT NOW -- do not call "
                "another tool just because you can, and do not gather extra information the user "
                "didn't ask for (e.g. don't re-check something you already just checked, don't look "
                "up unrelated facts). Only call another tool if the ORIGINAL request has a part that "
                "genuinely still isn't answered yet. When in doubt, answer now rather than continue."
            ),
        })

    # Exceeded MAX_STEPS -- stop looping and force a final answer from
    # whatever was actually gathered, rather than looping forever.
    print(f"[AGENT] Hit MAX_STEPS ({MAX_STEPS}) -- finalizing with what was gathered.")
    return _finalize_answer(question, context, steps_taken)


if __name__ == "__main__":
    for q in ["What is 27 × 43?", "Tell me a joke."]:
        print(f"\nUSER: {q}")
        print(f"JARVIS: {run_agent(q)}")