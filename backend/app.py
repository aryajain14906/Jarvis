from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from llm import ask_llm
from agent import run_agent
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
#
# NOTE: dropped `chroma_path` here -- memory.py is SQLite-only (no ChromaDB),
# so that kwarg didn't exist on MemoryManager and would raise a TypeError.
memory = MemoryManager(
    db_path="jarvis_memory.db",
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
    #    Local embedding + SQLite lookup, capped in size -- negligible latency.
    context = memory.build_context(question)

    # 2. Route through the agent: it decides tool-vs-direct-answer, then
    #    calls the local Ollama model (same persona/model as before).
    answer = run_agent(question, context=context)

    # 3. Update memory AFTER responding, in the background, so fact
    #    extraction / embedding never delays what the user hears.
    #    (Still uses plain ask_llm here -- fact extraction doesn't need
    #    the agent's tool-routing behavior.)
    background_tasks.add_task(memory.record_turn, question, answer, ask_llm)

    return {"answer": answer}


@app.post("/speak")
def speak(request: SpeakRequest):
    try:
        audio_base64 = generate_jarvis_audio_base64(request.text)
        return {
            "audio_base64": audio_base64,
            "audio_format": "wav",  # pyttsx3/SAPI5 outputs wav, not mp3
        }
    except Exception as e:
        return {"error": str(e)}