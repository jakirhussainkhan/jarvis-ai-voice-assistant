"""Conversation memory and long-term facts for Jarvis.

History is stored in a small SQLite database instead of an in-memory deque, so a
conversation survives a restart. Only the most recent turns are read back for the
model (:meth:`Memory.get_transcript`), which keeps the prompt small and therefore
cheap on the token-limited Groq free tier.

The same database also holds :class:`Memory`'s ``user_facts`` table. Facts are
kept separate from the rolling transcript, survive ``clear()``, and are read back
by the agent as a small synthetic memory message so the static system prompt can
stay unchanged for prefix caching.
"""

import os
import re
import sqlite3
import threading
from datetime import datetime

import logger
from config import MAX_HISTORY_MSGS

_ROLE_LABELS = {
    "user": "User",
    "assistant": "Jarvis",
    "model": "Jarvis",
}

# ChromaDB semantic memory. Both the client and the embedder are loaded lazily:
# the SQLite tables above remain the source of truth, and a missing package, a
# locked database or a failed embedding model simply disables the semantic
# index instead of breaking Jarvis.
_CHROMA_COLLECTION = "jarvis_memories"
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_SEMANTIC_OFF = frozenset({"0", "false", "no", "off"})

# Common words carry no retrieval signal in the SQLite fallback. The ChromaDB
# path uses real embeddings instead.
_FALLBACK_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "your", "yours", "what",
    "when", "where", "who", "why", "how", "does", "did", "was", "were", "with",
    "about", "from", "that", "this", "these", "those", "have", "has", "had",
    "can", "could", "would", "should", "tell", "know", "remember", "please",
    "jarvis", "sir", "me", "my", "i", "is", "it", "of", "to", "in", "on",
    "a", "an", "do", "be", "am",
})

_EMBEDDER = None
_EMBEDDER_FAILED = False
_EMBEDDER_LOCK = threading.Lock()


def default_chroma_path():
    """Where the persistent ChromaDB store lives."""
    override = (os.getenv("JARVIS_CHROMA_DB") or "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".jarvis", "chroma_db")


def _load_default_embedder():
    """Load fastembed's MiniLM model once, or return None if unavailable.

    ``fastembed`` is already a Jarvis dependency (the semantic answer cache uses
    it as well), so semantic memory does not add a second embedding stack.
    """
    global _EMBEDDER, _EMBEDDER_FAILED
    with _EMBEDDER_LOCK:
        if _EMBEDDER_FAILED:
            return None
        if _EMBEDDER is not None:
            return _EMBEDDER
        try:
            from fastembed import TextEmbedding

            model = TextEmbedding(model_name=_EMBED_MODEL)
            _EMBEDDER = lambda texts: [list(vector) for vector in model.embed(texts)]
        except Exception:
            _EMBEDDER_FAILED = True
            return None
    return _EMBEDDER

# Fallback store for memory tools called outside an agent turn (direct calls and
# tests). Inside run_agent(), tools use the conversation's Memory instance via a
# ContextVar instead. Both paths point at the same SQLite database in production,
# so facts persist across turns and sessions.
_DEFAULT_MEMORY = None
_DEFAULT_MEMORY_LOCK = threading.Lock()


def default_db_path():
    """Where the conversation database lives (override with JARVIS_MEMORY_DB)."""
    override = os.getenv("JARVIS_MEMORY_DB")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".jarvis", "memory.db")


class Memory:
    """Persistent conversation history backed by SQLite.

    One connection is shared and guarded by a lock because the GUI drives the
    same instance from a worker thread while ``main.py`` uses it from the main
    thread. ``:memory:`` is accepted (used by tests) and behaves like a normal,
    non-persistent database.
    """

    def __init__(self, db_path=None, limit=MAX_HISTORY_MSGS, *,
                 chroma_path=None, chroma_client=None, embedder=None,
                 semantic=None):
        self.path = db_path or default_db_path()
        self.limit = limit
        self._lock = threading.RLock()
        self._closed = False
        self.chroma_path = chroma_path or default_chroma_path()
        self._chroma_client = chroma_client
        self._chroma = None
        self._chroma_failed = False
        self._embedder = embedder
        self._embedder_failed = False
        self._semantic_error_logged = False
        if semantic is None:
            # Injecting a client or embedder is an explicit request to use the
            # semantic store (this is how tests avoid touching a real database).
            semantic = (chroma_client is not None or embedder is not None
                        or (os.getenv("JARVIS_SEMANTIC_MEMORY", "1").strip().lower()
                            not in _SEMANTIC_OFF))
        self.semantic_enabled = bool(semantic)
        if self.path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False lets the GUI worker reuse this connection; the
        # lock below serialises access so that is safe.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  role TEXT NOT NULL,"
            "  content TEXT NOT NULL,"
            "  timestamp TEXT NOT NULL"
            ")"
        )
        # Long-term facts survive conversation clears and restarts. They are
        # deliberately kept separate from the rolling conversation transcript.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS user_facts ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  fact TEXT NOT NULL,"
            "  timestamp TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    # -- semantic index ----------------------------------------------------- #
    def _log_semantic_error(self, context, exc):
        """Log one semantic failure per Memory instance (never raises)."""
        if self._semantic_error_logged:
            return
        self._semantic_error_logged = True
        try:
            logger.log_command_error(context, exc)
        except Exception:
            pass

    def _ensure_chroma(self):
        """Return the ``jarvis_memories`` collection, or None on failure."""
        if not self.semantic_enabled or self._chroma_failed:
            return None
        with self._lock:
            if self._chroma is not None:
                return self._chroma
            try:
                client = self._chroma_client
                if client is None:
                    import chromadb

                    os.makedirs(self.chroma_path, exist_ok=True)
                    client = chromadb.PersistentClient(path=self.chroma_path)
                    self._chroma_client = client
                try:
                    self._chroma = client.get_or_create_collection(
                        name=_CHROMA_COLLECTION,
                        metadata={"hnsw:space": "cosine"},
                    )
                except (TypeError, ValueError):
                    # Newer Chroma releases configure distance differently.
                    # The default L2 metric ranks normalised MiniLM vectors the
                    # same way, so the collection remains usable.
                    self._chroma = client.get_or_create_collection(
                        name=_CHROMA_COLLECTION
                    )
                return self._chroma
            except Exception as exc:
                self._chroma_failed = True
                self._log_semantic_error("ChromaDB memory initialization failed", exc)
                return None

    def _ensure_embedder(self):
        """Return a ``texts -> vectors`` callable, or None when unavailable."""
        if not self.semantic_enabled or self._embedder_failed:
            return None
        if self._embedder is not None:
            return self._embedder
        try:
            self._embedder = _load_default_embedder()
        except Exception as exc:
            self._log_semantic_error("Memory embedding model failed to load", exc)
            self._embedder = None
        if self._embedder is None:
            self._embedder_failed = True
            self._log_semantic_error(
                "Memory embedding model is unavailable",
                RuntimeError("install fastembed to enable semantic memory"),
            )
        return self._embedder

    def _embed(self, texts):
        """Generate one embedding per text; None when embeddings are unavailable."""
        embedder = self._ensure_embedder()
        if embedder is None:
            return None
        try:
            vectors = [[float(value) for value in vector]
                       for vector in embedder(list(texts))]
            if len(vectors) != len(texts) or not all(vectors):
                raise ValueError("embedder returned an unexpected shape")
            return vectors
        except Exception as exc:
            self._embedder_failed = True
            self._log_semantic_error("Memory embedding failed", exc)
            return None

    def _index_memory(self, doc_id, text, kind):
        """Store one document in ChromaDB; failures leave SQLite untouched."""
        if not self.semantic_enabled:
            return False
        collection = self._ensure_chroma()
        if collection is None:
            return False
        vectors = self._embed([text])
        if not vectors:
            return False
        try:
            collection.add(
                ids=[doc_id],
                documents=[text],
                embeddings=vectors,
                metadatas=[{"kind": kind,
                            "timestamp": datetime.now().isoformat(timespec="seconds")}],
            )
            return True
        except Exception as exc:
            self._log_semantic_error("ChromaDB memory write failed", exc)
            return False

    def _delete_semantic(self, ids=None, where=None):
        """Best-effort removal from ChromaDB; SQLite deletion already happened."""
        if not self.semantic_enabled or (not ids and not where):
            return False
        collection = self._ensure_chroma()
        if collection is None:
            return False
        try:
            if ids:
                collection.delete(ids=list(ids))
            elif where:
                collection.delete(where=where)
            return True
        except Exception as exc:
            self._log_semantic_error("ChromaDB memory delete failed", exc)
            return False

    @staticmethod
    def _clean_memories(documents, limit):
        """Flatten Chroma's nested ``documents`` result into clean text."""
        flat = []
        if documents:
            if isinstance(documents[0], str):
                flat = list(documents)
            else:
                for group in documents:
                    flat.extend(group or [])
        cleaned = []
        for item in flat:
            text = " ".join(str(item or "").split())
            if text and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _fallback_relevant(self, query, limit):
        """SQLite fallback: keyword matches first, then the newest facts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT fact FROM user_facts ORDER BY id DESC"
            ).fetchall()
        facts = [" ".join(str(row[0] or "").split()) for row in rows]
        facts = [fact for fact in facts if fact]
        words = {
            word for word in re.findall(r"[a-z0-9']+", (query or "").lower())
            if len(word) > 2 and word not in _FALLBACK_STOPWORDS
        }
        ranked = [fact for fact in facts
                  if any(word in fact.lower() for word in words)]
        result = []
        for fact in ranked + facts:
            if fact not in result:
                result.append(fact)
            if len(result) >= limit:
                break
        return result

    def retrieve_relevant_memories(self, query, limit=3):
        """Return up to ``limit`` memories semantically similar to ``query``.

        Uses ChromaDB when it is available and transparently falls back to a
        keyword/recent-fact SQLite search otherwise. Never raises.
        """
        query = " ".join(str(query or "").split())
        if not query:
            return []
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = 3

        collection = self._ensure_chroma()
        if collection is not None:
            vectors = self._embed([query])
            if vectors:
                try:
                    result = collection.query(
                        query_embeddings=vectors, n_results=limit
                    )
                    if isinstance(result, dict):
                        documents = result.get("documents")
                    else:
                        documents = getattr(result, "documents", None)
                    cleaned = self._clean_memories(documents, limit)
                    if cleaned:
                        return cleaned
                    # An empty semantic index (for example after upgrading from
                    # the SQLite-only version) should not hide known facts.
                except Exception as exc:
                    self._log_semantic_error("ChromaDB memory query failed", exc)
                    # Fall through to SQLite rather than losing the context.
        try:
            return self._fallback_relevant(query, limit)
        except Exception as exc:
            self._log_semantic_error("SQLite memory retrieval failed", exc)
            return []

    def add(self, role, text):
        """Append a single turn and persist it."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO conversations (role, content, timestamp) "
                "VALUES (?, ?, ?)",
                (role, text, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()
            row_id = cursor.lastrowid
        self._index_memory(f"turn-{row_id}", str(text), "turn")

    def add_fact(self, fact: str):
        """Persist one long-term fact about the user.

        Empty/whitespace-only facts are ignored. Returns ``True`` when a row was
        inserted, otherwise ``False``.
        """
        fact = " ".join(str(fact or "").split())
        if not fact:
            return False
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO user_facts (fact, timestamp) VALUES (?, ?)",
                (fact, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()
            row_id = cursor.lastrowid
        self._index_memory(f"fact-{row_id}", fact, "fact")
        return True

    def get_facts(self, limit: int = 15):
        """Return up to ``limit`` recent facts, newest first.

        Pass ``None`` to retrieve every saved fact (used by ``fn_recall_facts``).
        """
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT fact FROM user_facts ORDER BY id DESC"
                ).fetchall()
            else:
                try:
                    limit = max(0, int(limit))
                except (TypeError, ValueError):
                    limit = 15
                if limit == 0:
                    return []
                rows = self._conn.execute(
                    "SELECT fact FROM user_facts ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [row[0] for row in rows]

    def clear_facts(self):
        """Delete every saved long-term fact."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM user_facts").fetchall()
            self._conn.execute("DELETE FROM user_facts")
            self._conn.commit()
        self._delete_semantic(ids=[f"fact-{row[0]}" for row in rows])

    def delete_fact(self, keyword: str):
        """Delete saved facts whose text contains ``keyword``.

        Matching is case-insensitive for ASCII text (SQLite's ``LIKE``), so
        ``delete_fact("jakir")`` also removes "my name is Jakir". Returns the
        number of rows removed; an empty keyword deletes nothing.
        """
        keyword = " ".join(str(keyword or "").split())
        if not keyword:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM user_facts WHERE fact LIKE ?", (f"%{keyword}%",)
            ).fetchall()
            cursor = self._conn.execute(
                "DELETE FROM user_facts WHERE fact LIKE ?", (f"%{keyword}%",)
            )
            self._conn.commit()
        self._delete_semantic(ids=[f"fact-{row[0]}" for row in rows])
        return max(0, int(cursor.rowcount or 0))

    def get_transcript(self, limit=None):
        """Return the last ``limit`` turns as a readable transcript."""
        limit = self.limit if limit is None else limit
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM conversations "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows.reverse()  # newest-first query -> oldest-first transcript
        return "\n".join(
            f"{_ROLE_LABELS.get(role, role)}: {content}" for role, content in rows
        )

    def as_text(self):
        """Render the recent turns for the agent prompt (alias of get_transcript)."""
        return self.get_transcript()

    def clear(self):
        """Drop all stored turns (long-term facts are kept)."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM conversations").fetchall()
            self._conn.execute("DELETE FROM conversations")
            self._conn.commit()
        self._delete_semantic(ids=[f"turn-{row[0]}" for row in rows])

    @property
    def closed(self):
        """True after :meth:`close` has been called."""
        return self._closed

    def close(self):
        """Close the underlying database connection."""
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True


def default_memory():
    """Return the shared Memory store used by the memory tools.

    The database path is checked on every call so tests and environment changes
    cannot accidentally keep using an old store. With the default on-disk path,
    facts persist across processes and sessions.
    """
    global _DEFAULT_MEMORY
    path = default_db_path()
    with _DEFAULT_MEMORY_LOCK:
        if (_DEFAULT_MEMORY is None
                or _DEFAULT_MEMORY.path != path
                or _DEFAULT_MEMORY.closed):
            _DEFAULT_MEMORY = Memory(db_path=path)
        return _DEFAULT_MEMORY


def reset_default_memory():
    """Close and clear the shared facts store (used by tests and session resets)."""
    global _DEFAULT_MEMORY
    with _DEFAULT_MEMORY_LOCK:
        if _DEFAULT_MEMORY is not None:
            try:
                _DEFAULT_MEMORY.close()
            except Exception:
                pass
        _DEFAULT_MEMORY = None
