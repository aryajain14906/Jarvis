from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from llm import ask_llm
from tts import generate_jarvis_audio_base64
from memory import MemoryManager

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One MemoryManager per user. For a single-user home JARVIS this is fine
# as a module-level singleton; for multi-user, key a dict by user_id/session_id.
memory = MemoryManager(
    db_path="jarvis_memory.db",
    chroma_path="jarvis_chroma",
    user_id="default_user",
    short_term_turns=8,
)


class ChatRequest(BaseModel):
    question: str


class SpeakRequest(BaseModel):
    text: str


@app.get("/")
def home():
    return {"message": "Jarvis Backend is Running!"}


@app.post("/chat")
def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    question = request.question

    # 1. Build memory context BEFORE calling the LLM (facts + recent turns).
    #    This is the retrieval step -- capped in size, so it adds negligible
    #    latency (local embedding + SQLite lookup, no network calls).
    context = memory.build_context(question)

    # 2. Call the LLM with the question + injected memory context.
    #    See llm.py for how `context` gets folded into the system prompt.
    answer = ask_llm(question, context=context)

    # 3. Update memory AFTER responding, in the background, so fact
    #    extraction / embedding never delays what the user hears.
    #    Pass ask_llm itself as the fallback extractor so the LLM can catch
    #    facts the regex heuristics miss (uses a separate short prompt).
    background_tasks.add_task(memory.record_turn, question, answer, ask_llm)

    return {"answer": answer}


@app.post("/speak")
def speak(request: SpeakRequest):
    try:
        audio_base64 = generate_jarvis_audio_base64(request.text)
        return {
            "audio_base64": audio_base64,
            "audio_format": "mp3"
        }
    except Exception as e:
        return {"error": str(e)}
