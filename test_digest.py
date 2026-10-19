"""Tests for large-output digests in vision and web fetches."""

import types

import digest
import net
import vision

_LONG = ("This is a factual sentence with a number 42. " * 400)


class _Completions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = types.SimpleNamespace(content="Condensed facts only.")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _Chat:
    def __init__(self, calls):
        self.completions = _Completions(calls)


class _Client:
    def __init__(self, calls):
        self.chat = _Chat(calls)


def test_short_text_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(digest, "_client", None)
    assert digest.digest_text("A short answer.") == "A short answer."


def test_large_text_uses_the_cheap_model(monkeypatch):
    calls = []
    monkeypatch.setattr(digest, "_client", _Client(calls))
    out = digest.digest_text(_LONG, "Summarize this page.")
    assert out == "Condensed facts only."
    assert calls and calls[0]["max_tokens"] == 120
    assert "Summarize this page." in calls[0]["messages"][1]["content"]


def test_large_text_falls_back_locally(monkeypatch):
    monkeypatch.setattr(digest, "_client", None)
    out = digest.digest_text(_LONG, "Summarize this page.")
    assert out.startswith("This is a factual sentence")
    assert len(out) <= digest.DIGEST_MAX_CHARS + 2


def test_fetch_page_digests_a_large_page(monkeypatch):
    markup = "<html><body>" + ("word " * 4000) + "</body></html>"
    seen = {}

    def fake_digest(text, prompt="Summarize the key facts."):
        seen["chars"] = len(text)
        seen["prompt"] = prompt
        return "digested page"

    monkeypatch.setattr(net, "_get", lambda url, **kw: markup)
    monkeypatch.setattr(net, "digest_text", fake_digest)

    assert net.fetch_page("https://example.com/long") == "digested page"
    assert seen["chars"] >= digest.DIGEST_THRESHOLD_CHARS
    assert "example.com" in seen["prompt"]


def test_look_digests_a_large_vision_answer(monkeypatch, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    seen = {}

    def fake_digest(text, prompt="Summarize the key facts."):
        seen["chars"] = len(text)
        seen["prompt"] = prompt
        return "digested screen"

    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: shot)
    monkeypatch.setattr(vision, "ask_image", lambda p, q, **k: "x" * 13_000)
    monkeypatch.setattr(vision, "digest_text", fake_digest)

    assert vision.look("what is on screen?") == "digested screen"
    assert seen["chars"] == 13_000
    assert "what is on screen?" in seen["prompt"]
    assert not shot.exists()
