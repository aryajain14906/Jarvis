"""
memory.py
=========
Hybrid memory system for a real-time voice assistant (JARVIS-style).
No vector-DB dependency -- everything lives in a single SQLite file.

Two tiers:
  1. ShortTermMemory  - in-RAM sliding window of the last N conversation turns.
                        Zero latency, no disk I/O.
  2. LongTermMemory   - persistent, queryable memory across sessions, all in
                        SQLite:
                        - `facts` table    -> structured, cheap, exact facts
                        - `episodes` table -> raw exchanges
                        Both tables also store an embedding BLOB per row.
                        Semantic search = embed the query, then brute-force
                        cosine similarity in numpy over the stored vectors.
                        At personal-assistant scale (hundreds-to-low-thousands
                        of rows) this is faster than the I/O of a separate
                        vector DB process, and it's one file to back up.

Design goals:
  - Local-first: embeddings via sentence-transformers' all-MiniLM-L6-v2,
    downloaded once, runs on CPU in ~5-15ms per short string.
  - Fact extraction decoupled from the response path (call it from a
    FastAPI BackgroundTask) so it never adds latency to what the user hears.
  - Retrieval returns a small, budget-capped string ready to drop into a
    system prompt -- never raw DB rows.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from sentence_transformers import SentenceTransformer


# ------------------------------------------------------------------
# SHORT-TERM MEMORY  (in-RAM, per session)
# ------------------------------------------------------------------
@dataclass
class Turn:
    role: str      # "user" | "assistant"
    content: str
    ts: float = field(default_factory=time.time)


class ShortTermMemory:
    """
    Sliding window of the last `max_turns` exchanges.
    Cheap: this is just a deque, no disk/network involved.
    Feed this straight into the LLM's messages list every call.
    """

    def __init__(self, max_turns: int = 8):
        self.max_turns = max_turns
        self._buffer: deque[Turn] = deque(maxlen=max_turns)

    def add(self, role: str, content: str) -> None:
        self._buffer.append(Turn(role=role, content=content))

    def as_messages(self) -> list[dict]:
        """Return in the {"role":..., "content":...} shape the LLM API expects."""
        return [{"role": t.role, "content": t.content} for t in self._buffer]

    def as_text(self) -> str:
        return "\n".join(f"{t.role}: {t.content}" for t in self._buffer)

    def clear(self) -> None:
        self._buffer.clear()


# ------------------------------------------------------------------
# EMBEDDING MODEL (loaded once, shared by the whole process)
# ------------------------------------------------------------------
_EMBEDDER: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    global _EMBEDDER
    if _EMBEDDER is None:
        # ~80MB, CPU-friendly, first call downloads + caches it locally.
        _EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBEDDER


def embed(text: str) -> np.ndarray:
    vec = _get_embedder().encode(text, normalize_embeddings=True)
    return vec.astype(np.float32)


def cosine_top_k(query_vec: np.ndarray, vectors: np.ndarray, k: int) -> np.ndarray:
    """Both inputs assumed already L2-normalized -> dot product = cosine similarity."""
    if vectors.shape[0] == 0:
        return np.array([], dtype=int)
    sims = vectors @ query_vec
    k = min(k, len(sims))
    return np.argpartition(-sims, k - 1)[:k][np.argsort(-sims[np.argpartition(-sims, k - 1)[:k]])]


# ------------------------------------------------------------------
# LONG-TERM MEMORY  (SQLite only: facts + episodes + embeddings)
# ------------------------------------------------------------------
class LongTermMemory:
    def __init__(self, db_path: str = "jarvis_memory.db", user_id: str = "default_user"):
        self.user_id = user_id
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                category    TEXT NOT NULL,   -- preference | fact | event | task
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL,
                confidence  REAL DEFAULT 1.0,
                embedding   BLOB NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                user_text   TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                created_at  REAL NOT NULL,
                embedding   BLOB NOT NULL
            )
        """)
        self._conn.commit()

    # ---------------- WRITE PATH ----------------

    def store_fact(self, content: str, category: str = "fact", confidence: float = 1.0) -> str:
        fact_id = str(uuid.uuid4())
        vec = embed(content)
        self._conn.execute(
            "INSERT INTO facts (id, user_id, category, content, created_at, confidence, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fact_id, self.user_id, category, content, time.time(), confidence, vec.tobytes()),
        )
        self._conn.commit()
        return fact_id

    def store_episode(self, user_text: str, assistant_text: str) -> None:
        """Embed a whole exchange for later semantic recall ('what did we discuss about X')."""
        episode_id = str(uuid.uuid4())
        combined = f"User: {user_text}\nJarvis: {assistant_text}"
        vec = embed(combined)
        self._conn.execute(
            "INSERT INTO episodes (id, user_id, user_text, assistant_text, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (episode_id, self.user_id, user_text, assistant_text, time.time(), vec.tobytes()),
        )
        self._conn.commit()

    # ---------------- READ PATH ----------------

    def _fetch_vectors(self, table: str, text_col_expr: str) -> tuple[list[str], np.ndarray]:
        cur = self._conn.execute(
            f"SELECT {text_col_expr}, embedding FROM {table} WHERE user_id = ?",
            (self.user_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return [], np.empty((0, 384), dtype=np.float32)
        texts = [r[0] for r in rows]
        vectors = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        return texts, vectors

    def query(self, query_text: str, k_facts: int = 3, k_episodes: int = 2) -> str:
        """
        Semantic retrieval across facts + episodes, budget-capped and formatted
        for direct injection into a system prompt. Returns "" if nothing relevant.
        """
        qvec = embed(query_text)
        blocks: list[str] = []

        fact_texts, fact_vecs = self._fetch_vectors("facts", "content")
        if fact_texts:
            idx = cosine_top_k(qvec, fact_vecs, k_facts)
            hits = [fact_texts[i] for i in idx]
            if hits:
                blocks.append("Known facts about the user:\n" + "\n".join(f"- {h}" for h in hits))

        ep_texts, ep_vecs = self._fetch_vectors(
            "episodes", "user_text || ' -> ' || assistant_text"
        )
        if ep_texts:
            idx = cosine_top_k(qvec, ep_vecs, k_episodes)
            hits = [ep_texts[i] for i in idx]
            if hits:
                blocks.append("Relevant past exchanges:\n" + "\n".join(f"- {h}" for h in hits))

        return "\n\n".join(blocks)

    def all_facts(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT category, content, created_at FROM facts WHERE user_id = ? ORDER BY created_at DESC",
            (self.user_id,),
        )
        return [{"category": r[0], "content": r[1], "created_at": r[2]} for r in cur.fetchall()]


# ------------------------------------------------------------------
# FACT EXTRACTION
# ------------------------------------------------------------------
_HEURISTIC_PATTERNS = [
    (r"\bmy favorite (\w[\w\s]*?) is ([\w .,'-]+)", "preference"),
    (r"\bi (?:live|work) in ([\w .,'-]+)", "fact"),
    (r"\bmy name is ([\w .'-]+)", "fact"),
    (r"\bremind me to ([\w .,'-]+)", "task"),
    (r"\bi(?:'m| am) allergic to ([\w .,'-]+)", "fact"),
]


def extract_facts_heuristic(user_text: str) -> list[tuple[str, str]]:
    """Returns list of (content, category). Fast, regex-only, no LLM call."""
    found = []
    text = user_text.strip()
    for pattern, category in _HEURISTIC_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append((text, category))
    return found


def extract_facts_llm(user_text: str, assistant_text: str, llm_call) -> list[dict]:
    """
    Fallback/complementary extraction using the LLM itself for turns the
    heuristics miss. `llm_call` is any callable(prompt: str) -> str so this
    stays provider-agnostic (works with your existing ask_llm).

    Returns a list of {"content":..., "category":...} dicts, or [] if the
    model decides there's nothing worth storing.
    """
    prompt = f"""You extract durable personal facts from a conversation turn.

User said: "{user_text}"
Assistant replied: "{assistant_text}"

If the user's message contains a durable fact, preference, or task worth
remembering long-term (e.g. favorite things, personal details, ongoing
projects, recurring habits), return a JSON array of objects with keys
"content" and "category" (category is one of: preference, fact, event, task).

If there is nothing worth remembering (small talk, a one-off question,
transient context), return an empty JSON array: []

Respond with ONLY the JSON array, nothing else."""

    try:
        raw = llm_call(prompt)
        cleaned = raw.strip().strip("`").replace("json\n", "", 1)
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [p for p in parsed if isinstance(p, dict) and p.get("content")]
    except Exception:
        pass
    return []


# ------------------------------------------------------------------
# MEMORY MANAGER -- single object app.py talks to
# ------------------------------------------------------------------
class MemoryManager:
    """
    Orchestrates short-term + long-term memory so app.py only needs one object.

    Typical request cycle:
        ctx = memory.build_context(question)      # before calling the LLM
        answer = ask_llm(question, context=ctx)
        memory.record_turn(question, answer)       # after responding (can be backgrounded)
    """

    def __init__(self, db_path: str = "jarvis_memory.db", user_id: str = "default_user",
                 short_term_turns: int = 8):
        self.short_term = ShortTermMemory(max_turns=short_term_turns)
        self.long_term = LongTermMemory(db_path=db_path, user_id=user_id)

    def build_context(self, question: str, max_chars: int = 1200) -> str:
        """
        Assemble the memory block to inject into the system prompt.
        Capped in length so we never blow the context window or add
        noticeable prompt-processing latency.
        """
        recall = self.long_term.query(question)
        recent = self.short_term.as_text()

        parts = []
        if recall:
            parts.append(recall)
        if recent:
            parts.append(f"Recent conversation:\n{recent}")

        context = "\n\n".join(parts)
        return context[:max_chars]

    def record_turn(self, user_text: str, assistant_text: str, llm_call=None) -> None:
        """
        Update short-term buffer immediately (cheap), then persist to
        long-term storage. If `llm_call` is provided, also runs LLM-based
        fact extraction as a fallback to the heuristics. Call this from a
        FastAPI BackgroundTask so it never blocks the spoken response.
        """
        self.short_term.add("user", user_text)
        self.short_term.add("assistant", assistant_text)

        self.long_term.store_episode(user_text, assistant_text)

        for content, category in extract_facts_heuristic(user_text):
            self.long_term.store_fact(content, category=category)

        if llm_call is not None:
            for fact in extract_facts_llm(user_text, assistant_text, llm_call):
                self.long_term.store_fact(fact["content"], category=fact.get("category", "fact"))
