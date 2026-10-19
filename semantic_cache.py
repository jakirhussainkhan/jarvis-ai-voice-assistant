"""Semantic answer cache for the agent.

The existing exact-match cache only helps when the user repeats a query
character-for-character. This cache embeds each cacheable question with
``all-MiniLM-L6-v2`` and returns an earlier answer when the cosine similarity is
at least 0.90.

Embeddings are stored next to the conversation history in SQLite and mirrored in
memory for fast comparisons. The embedder is lazy: if ``fastembed`` is not
installed (or the model cannot be loaded), the cache degrades to a no-op and the
agent keeps working exactly as before.

Dynamic questions (time, weather, news, system status, actions) are never cached
semantically, because a stale answer would be wrong.
"""

import array
import math
import os
import re
import sqlite3
import threading
import time

from memory import default_db_path

_CLOSED_VALUES = frozenset({"0", "false", "no", "off"})
MODEL_NAME = "all-MiniLM-L6-v2"
_MODEL_ID = f"sentence-transformers/{MODEL_NAME}"

# Questions whose answer changes with the clock, the machine, the web or an
# action. These are deliberately conservative: a cache miss only costs a normal
# API call, while a stale hit can be plainly wrong.
_DYNAMIC_RE = re.compile(
    r"\b(now|today|tonight|tomorrow|yesterday|current|currently|latest|recent|"
    r"news|weather|forecast|temperature|time|date|day|year|price|prices|stock|"
    r"scores?|schedule|calendar|remind|reminder|timer|alarm|playing|volume|"
    r"president|minister|ceo|leader|winner|champion|population|ranking|version|"
    r"release|cost|worth|exchange rate|"
    r"brightness|battery|cpu|ram|disk|memory|status|screen|screenshot|window|"
    r"file|files|folder|download|upload|send|email|message|open|close|launch|"
    r"run|execute|play|pause|stop|set|turn|lock|unlock|take|show|list|switch|"
    r"click|type|press|scroll|delete|remove|create|write|save|move|copy|"
    r"rename|install|update|kill|shutdown|restart|my|our)\b",
    re.I,
)

_ERROR_PREFIXES = (
    "sorry",
    "i couldn't",
    "i couldn’t",
    "i can't",
    "i can’t",
    "i am having trouble",
    "refused",
    "error",
)


def _normalise(query):
    return " ".join(str(query or "").lower().split())


def _cosine(a, b):
    """Cosine similarity for two equal-length float sequences."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """A small SQLite-backed semantic answer cache.

    ``embedder`` is injectable so tests can run without downloading a model. Pass
    ``enabled=False`` to disable it entirely; by default the value of
    ``JARVIS_SEMANTIC_CACHE`` decides (1/on, 0/off).
    """

    def __init__(self, db_path=None, threshold=0.90, embedder=None,
                 enabled=None, max_entries=512):
        self.threshold = float(threshold)
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._embedder = embedder
        self._embedder_failed = False
        self._entries = []
        self._conn = None
        self.path = db_path or default_db_path()
        if enabled is None:
            # Injecting an embedder is an explicit request to use the cache
            # (this is how tests avoid downloading a model).
            enabled = embedder is not None or (
                os.getenv("JARVIS_SEMANTIC_CACHE", "1").strip().lower()
                not in _CLOSED_VALUES
            )
        self.enabled = bool(enabled)
        if not self.enabled:
            return

        try:
            if self.path != ":memory:":
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS semantic_cache ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  query TEXT NOT NULL,"
                "  reply TEXT NOT NULL,"
                "  embedding BLOB NOT NULL,"
                "  created_at REAL NOT NULL"
                ")"
            )
            self._conn.commit()
            self._load()
        except Exception:
            # The cache is an optimisation: a read-only HOME or a locked DB must
            # not stop Jarvis from starting or answering.
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None
            self._entries = []
            self.enabled = False

    # -- public API -------------------------------------------------------- #
    @staticmethod
    def is_cacheable(query):
        """True when a query is stable enough to cache semantically."""
        text = _normalise(query)
        if len(text) < 8 or len(text) > 500:
            return False
        return _DYNAMIC_RE.search(text) is None

    def lookup(self, query):
        """Return a cached reply for a similar cacheable query, or None."""
        try:
            return self._lookup(query)
        except Exception:
            return None

    def _lookup(self, query):
        if not self.enabled or not self.is_cacheable(query):
            return None
        with self._lock:
            snapshot = list(self._entries)
        if not snapshot:
            # Do not load the embedding model just to search an empty cache.
            return None
        vector = self._embed(_normalise(query))
        if vector is None:
            return None

        best_score = 0.0
        best_reply = None
        for _row_id, _cached_query, reply, cached_vector in snapshot:
            score = _cosine(vector, cached_vector)
            if score > best_score:
                best_score = score
                best_reply = reply
        if best_score >= self.threshold:
            return best_reply
        return None

    def store(self, query, reply):
        """Cache ``reply`` for ``query`` when both are suitable."""
        try:
            return self._store(query, reply)
        except Exception:
            return False

    def _store(self, query, reply):
        if not self.enabled or not reply:
            return False
        text = _normalise(query)
        if not self.is_cacheable(text):
            return False
        if str(reply).strip().lower().startswith(_ERROR_PREFIXES):
            return False

        vector = self._embed(text)
        if vector is None:
            return False
        blob = array.array("f", vector).tobytes()
        now = time.time()
        with self._lock:
            if self._conn is None:
                return False
            for index, (row_id, cached_query, _old_reply, _vec) in enumerate(
                    self._entries):
                if cached_query == text:
                    self._conn.execute(
                        "UPDATE semantic_cache SET reply=?, embedding=?, "
                        "created_at=? WHERE id=?",
                        (str(reply), blob, now, row_id),
                    )
                    self._conn.commit()
                    self._entries[index] = (row_id, text, str(reply), vector)
                    return True

            cursor = self._conn.execute(
                "INSERT INTO semantic_cache (query, reply, embedding, created_at) "
                "VALUES (?, ?, ?, ?)",
                (text, str(reply), blob, now),
            )
            self._conn.commit()
            self._entries.append((cursor.lastrowid, text, str(reply), vector))
            self._trim()
        return True

    # Convenience aliases for callers that think in cache terms.
    add = store
    get = lookup

    def clear(self):
        """Remove every semantic-cache entry."""
        with self._lock:
            self._entries.clear()
            if self._conn is not None:
                self._conn.execute("DELETE FROM semantic_cache")
                self._conn.commit()

    def close(self):
        """Close the SQLite connection (the cache is unusable afterwards)."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._entries.clear()

    def __len__(self):
        with self._lock:
            return len(self._entries)

    # -- internals --------------------------------------------------------- #
    def _load(self):
        rows = self._conn.execute(
            "SELECT id, query, reply, embedding FROM semantic_cache ORDER BY id"
        ).fetchall()
        entries = []
        for row_id, query, reply, blob in rows:
            values = array.array("f")
            try:
                values.frombytes(blob)
            except (TypeError, ValueError):
                continue
            entries.append((row_id, query, reply, list(values)))
        self._entries = entries[-self.max_entries:]

    def _trim(self):
        while len(self._entries) > self.max_entries:
            row_id, _query, _reply, _vector = self._entries.pop(0)
            self._conn.execute("DELETE FROM semantic_cache WHERE id=?", (row_id,))
        self._conn.commit()

    def _embed(self, text):
        """Embed ``text`` lazily; returns None when no embedder is available."""
        if self._embedder is None and not self._embedder_failed:
            self._embedder = self._load_embedder()
            if self._embedder is None:
                self._embedder_failed = True
        if self._embedder is None:
            return None
        try:
            vector = self._embedder(text)
        except Exception:
            return None
        if vector is None:
            return None
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError):
            return None
        return values or None

    @staticmethod
    def _load_embedder():
        """Load fastembed's MiniLM model, or None when it is unavailable."""
        try:
            from fastembed import TextEmbedding
            model = TextEmbedding(model_name=_MODEL_ID)
            return lambda text: list(model.embed([text]))[0].tolist()
        except Exception:
            return None
