"""Tests for the SQLite-backed memory.Memory."""

import os

import agent
import memory as memory_module
import tools
from memory import Memory, default_db_path


class _FakeCollection:
    """Minimal Chroma collection double (add/query/delete only)."""

    def __init__(self):
        self.added = {"ids": [], "documents": [], "embeddings": [], "metadatas": []}
        self.deleted_ids = []
        self.queries = []
        self.query_documents = None
        self.query_error = None

    def add(self, ids, documents, embeddings, metadatas):
        self.added["ids"].extend(ids)
        self.added["documents"].extend(documents)
        self.added["embeddings"].extend(embeddings)
        self.added["metadatas"].extend(metadatas)

    def query(self, query_embeddings, n_results):
        self.queries.append(
            {"embeddings": query_embeddings, "n_results": n_results}
        )
        if self.query_error is not None:
            raise self.query_error
        documents = self.query_documents
        if documents is None:
            documents = self.added["documents"]
        return {"documents": [list(documents[:n_results])]}

    def delete(self, ids=None, where=None):
        self.deleted_ids.append(list(ids or []))


class _FakeClient:
    """Minimal persistent-client double that returns one collection."""

    def __init__(self, collection=None, error=None):
        self.collection = collection or _FakeCollection()
        self.error = error
        self.calls = []

    def get_or_create_collection(self, name, metadata=None):
        self.calls.append({"name": name, "metadata": metadata})
        if self.error is not None:
            raise self.error
        return self.collection


def _fake_embedder(texts):
    return [[float(len(text)), 1.0, 0.5] for text in texts]


def _semantic_memory(tmp_path, client=None):
    return Memory(
        db_path=str(tmp_path / "memory.db"),
        chroma_client=client or _FakeClient(),
        embedder=_fake_embedder,
    )


def test_add_and_render(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "hello")
    mem.add("assistant", "hi")
    assert mem.as_text() == "User: hello\nJarvis: hi"


def test_model_role_is_labelled_jarvis(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("model", "hi")
    assert mem.as_text() == "Jarvis: hi"


def test_transcript_is_windowed(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"), limit=2)
    mem.add("user", "a")
    mem.add("assistant", "b")
    mem.add("user", "c")
    # Only the last `limit` turns are returned, oldest first.
    assert mem.get_transcript() == "Jarvis: b\nUser: c"
    assert mem.get_transcript(limit=1) == "User: c"


def test_history_persists_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    first = Memory(db_path=path)
    first.add("user", "remember this")
    first.close()

    second = Memory(db_path=path)
    assert "remember this" in second.as_text()


def test_clear_empties_memory(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "x")
    mem.clear()
    assert mem.as_text() == ""


def test_default_path_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "custom.db"))
    assert default_db_path() == str(tmp_path / "custom.db")


def test_default_path_is_under_the_home_directory(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_DB", raising=False)
    home = "/home/tester"
    monkeypatch.setenv("HOME", home)
    if os.name == "nt":
        # expanduser() prefers USERPROFILE over HOME on Windows.
        monkeypatch.setenv("USERPROFILE", home)
    assert default_db_path() == os.path.join(home, ".jarvis", "memory.db")


# --------------------------------------------------------------------------- #
# long-term user facts
# --------------------------------------------------------------------------- #

def test_add_and_get_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    assert mem.add_fact("my name is Jakir") is True
    assert mem.add_fact("I prefer tea over coffee") is True

    # Newest facts come first.
    assert mem.get_facts() == [
        "I prefer tea over coffee",
        "my name is Jakir",
    ]


def test_empty_fact_is_ignored(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    assert mem.add_fact("   ") is False
    assert mem.get_facts() == []


def test_facts_are_limited_to_the_most_recent(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    for index in range(20):
        mem.add_fact(f"fact {index}")

    facts = mem.get_facts(limit=15)
    assert len(facts) == 15
    assert facts[0] == "fact 19"
    assert facts[-1] == "fact 5"
    assert "fact 4" not in facts
    assert len(mem.get_facts(limit=None)) == 20
    assert mem.get_facts(limit=0) == []


def test_facts_persist_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    first = Memory(db_path=path)
    first.add_fact("my birthday is in May")
    first.close()

    second = Memory(db_path=path)
    assert second.get_facts() == ["my birthday is in May"]


def test_clear_history_keeps_long_term_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "hello")
    mem.add_fact("my name is Jakir")
    mem.clear()

    assert mem.as_text() == ""
    assert mem.get_facts() == ["my name is Jakir"]

    mem.clear_facts()
    assert mem.get_facts() == []


# --------------------------------------------------------------------------- #
# forgetting / replacing facts
# --------------------------------------------------------------------------- #

def test_delete_fact_removes_only_matching_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add_fact("my name is Jakir")
    mem.add_fact("I prefer tea over coffee")

    assert mem.delete_fact("Jakir") == 1
    assert mem.get_facts() == ["I prefer tea over coffee"]


def test_delete_fact_is_case_insensitive_and_substring(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add_fact("my name is Jakir")
    mem.add_fact("my birthday is in May")

    assert mem.delete_fact("name") == 1
    assert mem.get_facts() == ["my birthday is in May"]

    mem.add_fact("MY NAME IS JAKIR")
    assert mem.delete_fact("my name") == 1
    assert mem.get_facts() == ["my birthday is in May"]


def test_delete_fact_returns_zero_when_nothing_matches(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add_fact("I prefer tea over coffee")

    assert mem.delete_fact("Jakir") == 0
    assert mem.get_facts() == ["I prefer tea over coffee"]


def test_delete_fact_with_empty_keyword_deletes_nothing(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add_fact("my name is Jakir")

    assert mem.delete_fact("") == 0
    assert mem.delete_fact("   ") == 0
    assert mem.get_facts() == ["my name is Jakir"]


def test_deleted_fact_does_not_come_back_after_restart(tmp_path):
    path = str(tmp_path / "m.db")
    first = Memory(db_path=path)
    first.add_fact("my name is Jakir")
    first.add_fact("my name is John")
    assert first.delete_fact("Jakir") == 1
    first.close()

    second = Memory(db_path=path)
    assert second.get_facts() == ["my name is John"]


# --------------------------------------------------------------------------- #
# ChromaDB semantic memory
# --------------------------------------------------------------------------- #

def test_chroma_collection_is_created_lazily(tmp_path):
    client = _FakeClient()
    mem = _semantic_memory(tmp_path, client)

    assert client.calls == []          # construction itself does no I/O
    mem.add_fact("my name is Jakir")

    assert client.calls[0]["name"] == "jarvis_memories"
    assert client.calls[0]["metadata"] == {"hnsw:space": "cosine"}


def test_add_fact_indexes_text_with_embedding(tmp_path):
    collection = _FakeCollection()
    mem = _semantic_memory(tmp_path, _FakeClient(collection))

    assert mem.add_fact("my name is Jakir") is True

    assert collection.added["ids"] == ["fact-1"]
    assert collection.added["documents"] == ["my name is Jakir"]
    assert collection.added["embeddings"] == [_fake_embedder(["my name is Jakir"])[0]]
    assert collection.added["metadatas"][0]["kind"] == "fact"


def test_add_conversation_turn_is_indexed_too(tmp_path):
    collection = _FakeCollection()
    mem = _semantic_memory(tmp_path, _FakeClient(collection))

    mem.add("user", "I like green tea")

    assert collection.added["ids"] == ["turn-1"]
    assert collection.added["metadatas"][0]["kind"] == "turn"


def test_retrieve_relevant_memories_queries_chroma_with_limit(tmp_path):
    collection = _FakeCollection()
    collection.query_documents = [
        "my birthday is in May",
        "I prefer tea over coffee",
        "my name is Jakir",
    ]
    mem = _semantic_memory(tmp_path, _FakeClient(collection))

    result = mem.retrieve_relevant_memories("when is my birthday", limit=2)

    assert result == ["my birthday is in May", "I prefer tea over coffee"]
    assert collection.queries == [{
        "embeddings": [_fake_embedder(["when is my birthday"])[0]],
        "n_results": 2,
    }]


def test_retrieve_relevant_memories_falls_back_to_sqlite(tmp_path):
    collection = _FakeCollection()
    collection.query_error = RuntimeError("index is corrupt")
    mem = _semantic_memory(tmp_path, _FakeClient(collection))
    mem.add_fact("my name is Jakir")
    mem.add_fact("I prefer tea over coffee")

    result = mem.retrieve_relevant_memories("coffee", limit=3)

    assert result[0] == "I prefer tea over coffee"
    assert "my name is Jakir" in result


def test_retrieve_fallback_logs_the_chroma_failure(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("JARVIS_LOG_DIR", str(log_dir))
    collection = _FakeCollection()
    collection.query_error = RuntimeError("index is corrupt")
    mem = _semantic_memory(tmp_path, _FakeClient(collection))
    mem.add_fact("I prefer tea over coffee")

    assert mem.retrieve_relevant_memories("coffee") == ["I prefer tea over coffee"]

    log = (log_dir / "command_errors.log").read_text(encoding="utf-8")
    assert "ChromaDB memory query failed" in log
    assert "RuntimeError: index is corrupt" in log


def test_chroma_initialization_failure_keeps_facts_usable(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("JARVIS_LOG_DIR", str(log_dir))
    mem = _semantic_memory(
        tmp_path, _FakeClient(error=RuntimeError("cannot open database"))
    )

    assert mem.add_fact("my name is Jakir") is True
    assert mem.get_facts() == ["my name is Jakir"]
    assert mem.retrieve_relevant_memories("name") == ["my name is Jakir"]

    log = (log_dir / "command_errors.log").read_text(encoding="utf-8")
    assert "ChromaDB memory initialization failed" in log
    assert "cannot open database" in log


def test_semantic_memory_can_be_disabled(tmp_path):
    client = _FakeClient()
    mem = Memory(
        db_path=str(tmp_path / "memory.db"),
        chroma_client=client,
        embedder=_fake_embedder,
        semantic=False,
    )
    mem.add_fact("my name is Jakir")

    assert client.calls == []
    assert mem.retrieve_relevant_memories("name") == ["my name is Jakir"]


def test_deleting_a_fact_removes_its_chroma_document(tmp_path):
    collection = _FakeCollection()
    mem = _semantic_memory(tmp_path, _FakeClient(collection))
    mem.add_fact("my name is Jakir")
    mem.add_fact("I prefer tea over coffee")

    assert mem.delete_fact("Jakir") == 1
    assert collection.deleted_ids == [["fact-1"]]

    mem.clear_facts()
    assert collection.deleted_ids[-1] == ["fact-2"]


def test_default_chroma_path_honours_the_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_CHROMA_DB", "/tmp/embeddings")
    assert memory_module.default_chroma_path() == "/tmp/embeddings"


def test_default_chroma_path_is_under_the_home_directory(monkeypatch):
    monkeypatch.delenv("JARVIS_CHROMA_DB", raising=False)
    # Windows uses USERPROFILE for expanduser("~"); POSIX uses HOME.
    if os.name == "nt":
        home = r"C:\Users\tester"
        monkeypatch.setenv("USERPROFILE", home)
    else:
        home = "/home/tester"
        monkeypatch.setenv("HOME", home)
    expected = os.path.join(home, ".jarvis", "chroma_db")
    assert memory_module.default_chroma_path() == expected


# --------------------------------------------------------------------------- #
# agent / tool integration
# --------------------------------------------------------------------------- #

class _RetrievalMemory:
    def __init__(self, facts):
        self.facts = facts
        self.queries = []

    def retrieve_relevant_memories(self, query, limit=3):
        self.queries.append((query, limit))
        return self.facts[:limit]


def test_agent_memory_context_uses_relevant_query_and_limit():
    mem = _RetrievalMemory([
        "my birthday is in May",
        "I prefer tea over coffee",
        "my name is Jakir",
        "I live in Dhaka",
    ])

    context = agent._facts_context(mem, "when is my birthday")

    assert mem.queries == [("when is my birthday", 3)]
    assert "my birthday is in May" in context
    assert "I live in Dhaka" not in context
    assert context.count("- ") == 3


def test_agent_recall_facts_tool_uses_semantic_retrieval():
    mem = _RetrievalMemory(["my birthday is in May"])
    token = tools.set_active_memory(mem)
    try:
        out = tools.fn_recall_facts("birthday")
    finally:
        tools.reset_active_memory(token)

    assert mem.queries == [("birthday", 5)]
    assert "my birthday is in May" in out


def test_recall_facts_without_query_still_returns_everything(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    mem.add_fact("my name is Jakir")
    mem.add_fact("I prefer tea over coffee")
    token = tools.set_active_memory(mem)
    try:
        out = tools.fn_recall_facts()
    finally:
        tools.reset_active_memory(token)

    assert "my name is Jakir" in out
    assert "I prefer tea over coffee" in out
