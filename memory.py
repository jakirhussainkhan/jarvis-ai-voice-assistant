"""Conversation memory and persistence for Jarvis."""

from collections import deque

from config import MAX_HISTORY_MSGS

_ROLE_LABELS = {
    "user": "User",
    "assistant": "Jarvis",
    "model": "Jarvis",
}


class Memory:
    """Rolling window of recent conversation turns."""

    def __init__(self, maxlen=MAX_HISTORY_MSGS):
        self.turns = deque(maxlen=maxlen)

    def add(self, role, text):
        """Append a single turn to memory."""
        self.turns.append((role, text))

    def as_text(self):
        """Render the stored turns as a readable transcript."""
        return "\n".join(
            f"{_ROLE_LABELS.get(role, role)}: {text}" for role, text in self.turns
        )

    def clear(self):
        """Drop all stored turns."""
        self.turns.clear()
