# JARVIS

JARVIS is a local-first personal AI assistant built in Python. It combines a local language model with voice interaction, memory, tools, browser automation, and screen understanding.

The goal is to build an assistant that can understand a request, decide what needs to be done, use the appropriate tools, and verify the result.

## Features

- Local LLM using Ollama
- Wake-word activation
- Speech recognition with Whisper
- Text-to-speech with pyttsx3
- Agent-based tool selection and execution
- Multi-step tool usage
- System automation
- Browser automation with Playwright
- Persistent memory
- Screenshot capture
- Screen text extraction using Tesseract OCR
- Screen understanding using a local vision model
- Computer interaction
- Action verification
- Web-based JARVIS interface

## Architecture

JARVIS is divided into several main components:

- **LLM** — Handles reasoning and natural language.
- **Agent** — Decides when and how tools should be used.
- **Tools** — Perform actions on the computer and external applications.
- **Memory** — Stores and retrieves relevant information.
- **Voice** — Handles wake-word detection, speech recognition, and speech output.
- **Vision** — Captures and interprets the screen.
- **Frontend** — Provides the JARVIS interface.

The general flow is:

```text
User
 ↓
Voice / Frontend
 ↓
Agent
 ↓
LLM
 ↓
Tool Selection
 ↓
Tool Execution
 ↓
Result
 ↓
LLM
 ↓
Response

For screen-related tasks:

Screen
 ↓
Screenshot
 ↓
OCR / Vision / UI information
 ↓
Agent
 ↓
Action
 ↓
Verification
