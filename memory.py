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
import sqlite3
import threading
from datetime import datetime

from config import MAX_HISTORY_MSGS

_ROLE_LABELS = {
    "user": "User",
    "assistant": "Jarvis",
    "model": "Jarvis",
}

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

    def __init__(self, db_path=None, limit=MAX_HISTORY_MSGS):
        self.path = db_path or default_db_path()
        self.limit = limit
        self._lock = threading.RLock()
        self._closed = False
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

    def add(self, role, text):
        """Append a single turn and persist it."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (role, content, timestamp) "
                "VALUES (?, ?, ?)",
                (role, text, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def add_fact(self, fact: str):
        """Persist one long-term fact about the user.

        Empty/whitespace-only facts are ignored. Returns ``True`` when a row was
        inserted, otherwise ``False``.
        """
        fact = " ".join(str(fact or "").split())
        if not fact:
            return False
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_facts (fact, timestamp) VALUES (?, ?)",
                (fact, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()
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
            self._conn.execute("DELETE FROM user_facts")
            self._conn.commit()

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
            self._conn.execute("DELETE FROM conversations")
            self._conn.commit()

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
