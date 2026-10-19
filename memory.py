"""Conversation memory and persistence for Jarvis.

History is stored in a small SQLite database instead of an in-memory deque, so a
conversation survives a restart. Only the most recent turns are read back for the
model (:meth:`Memory.get_transcript`), which keeps the prompt small and therefore
cheap on the token-limited Groq free tier.
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
        """Drop all stored turns."""
        with self._lock:
            self._conn.execute("DELETE FROM conversations")
            self._conn.commit()

    def close(self):
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()
