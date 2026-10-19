"""Shared pytest configuration.

Keep a test run away from the developer's real data: the conversation database
is kept in memory, so tests never create or write ``~/.jarvis/memory.db``. A test
that needs real persistence passes its own ``db_path`` (see test_memory.py).

Semantic-cache embeddings are disabled by default so the suite never downloads a
model; tests that exercise the cache inject a fake embedder.
"""

import os

os.environ.setdefault("JARVIS_MEMORY_DB", ":memory:")
os.environ.setdefault("JARVIS_SEMANTIC_CACHE", "0")
