from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base64
import cv2
import numpy as np

from llm import ask_llm
from agent import run_agent
from tts import generate_jarvis_audio_base64
from memory import MemoryManager, set_shared_memory_manager
import face_auth

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
    user_id="default_user",
    short_term_turns=8,
)
# Makes this same MemoryManager reachable from tool/memory.py's remember_fact,
# without a circular import between app.py and the tool package.
set_shared_memory_manager(memory)

# Face-auth gate: a single-user local assistant only needs a simple module-level
# flag, same pattern as the memory/browser singletons elsewhere in this project.
AUTHORIZED = False


class ChatRequest(BaseModel):
    question: str


class SpeakRequest(BaseModel):
    text: str


class VerifyFaceRequest(BaseModel):
    image: str  # base64-encoded JPEG, no data-URL prefix


@app.get("/")
def home():
    return {"message": "Jarvis Backend is Running!"}


@app.post("/verify_face")
def verify_face(request: VerifyFaceRequest):
    global AUTHORIZED
    try:
        img_bytes = base64.b64decode(request.image)
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"authorized": False, "message": "Couldn't decode image."}

        ok, result = face_auth.verify_frame(frame)
        if ok:
            AUTHORIZED = True
            return {"authorized": True, "name": result, "greeting": f"Hello sir, systems online."}
        return {"authorized": False, "message": result}
    except Exception as e:
        return {"authorized": False, "message": f"Error: {e}"}


@app.post("/logout")
def logout():
    global AUTHORIZED
    AUTHORIZED = False
    return {"authorized": False}


@app.post("/chat")
def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    if not AUTHORIZED:
        return {"answer": "Access locked -- face verification required.", "locked": True}

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