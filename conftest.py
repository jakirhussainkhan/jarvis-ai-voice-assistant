"""Shared pytest configuration.

Keep a test run away from the developer's real data: the conversation database
is kept in memory, so tests never create or write ``~/.jarvis/memory.db``. A test
that needs real persistence passes its own ``db_path`` (see test_memory.py).
"""

import os

os.environ.setdefault("JARVIS_MEMORY_DB", ":memory:")
