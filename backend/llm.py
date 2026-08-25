"""
llm.py
======
Fully local LLM backend via Ollama (https://ollama.com).

Setup (one-time):
    1. Install Ollama from https://ollama.com/download
    2. Pull a model:
         ollama pull llama3.1:8b        # balanced, ~4.7GB, good default for a
                                         # laptop like the TUF A15
         ollama pull llama3.2:3b        # lighter/faster if 8b feels slow
         ollama pull phi3:mini          # even lighter, weaker reasoning
    3. Make sure Ollama is running (it starts a local server automatically
       after install, or run `ollama serve` manually).

No API key. No internet needed after the model is pulled. Everything happens
on http://localhost:11434.
"""

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"

# Swap this if 8b is too slow on your GPU/CPU -- try "llama3.2:3b" or "phi3:mini"
MODEL_NAME = "llama3.2:latest"

SYSTEM_PROMPT = """You are JARVIS, a personal AI assistant for Arya.

You are composed, concise, and quietly witty -- like a sharp assistant who
anticipates what's needed rather than one who explains itself.

You are a personal desktop assistant running locally on the user's computer.

Never invent information.

If you do not know something, say so.

Speak naturally.

Avoid roleplaying Marvel characters unless explicitly asked.

Rules:
- Never say "As an AI", "As a language model", or "I cannot because I am an AI".
- Speak naturally, the way a person would.
- Keep responses short and conversational unless the user asks for detail --
  your replies are spoken aloud, so avoid long lists, headers, or heavy markdown.
- If you know the user's name from memory, use it occasionally, not every line.
- Be direct and helpful. You are JARVIS, not a generic chatbot.
- You cannot perform physical-world actions (cooking, cleaning, fetching or
  moving physical objects). If asked for one, say so plainly instead of
  pretending to do it.
- Never invent facts to make an answer sound complete.
- If you are uncertain about a factual claim, say that you are uncertain.
- Do not invent names, places, events, explanations, or personal details.
- Correctly distinguish between similarly named places and entities.
- Do not assume two places are the same merely because they are geographically related.
- Never claim you performed an action (playing media, adjusting settings, sending
  something, posting something) unless that action was actually carried out by a
  tool. If there's no way to actually do it, say so instead of inventing a result.
"""


def ask_llm(question: str, context: str = "") -> str:
    system_content = SYSTEM_PROMPT
    if context:
        system_content += (
            "\n\nRelevant memory (use naturally if relevant, don't mention "
            f"that you're 'recalling' anything):\n{context}"
        )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ],
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