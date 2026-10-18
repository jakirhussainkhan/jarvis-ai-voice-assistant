"""Tests for the live-internet helpers (net.py) with the network mocked."""

import net

_DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=1">
  Example <b>Page</b></a>
<a class="result__snippet">A snippet about <i>example</i> things.</a>
<a class="result__a" href="https://direct.example/second">Second result</a>
<a class="result__snippet">Second snippet.</a>
</body></html>
"""


def test_html_to_text_strips_tags_and_scripts():
    text = net.html_to_text("<p>Hello <b>world</b></p><script>evil()</script>")
    assert "Hello" in text and "world" in text
    assert "evil" not in text
    assert "<" not in text


def test_search_parses_results_and_unwraps_redirect(monkeypatch):
    monkeypatch.setattr(net, "_get", lambda url, **kw: _DDG_HTML)
    results = net.search("example", limit=5)
    assert results[0]["title"] == "Example Page"
    assert results[0]["url"] == "https://example.com/page"
    assert "example" in results[0]["snippet"]
    assert results[1]["url"] == "https://direct.example/second"


def test_search_empty_query(monkeypatch):
    assert net.search("") == []


def test_search_handles_no_network(monkeypatch):
    monkeypatch.setattr(net, "_get", lambda url, **kw: None)
    assert net.search("anything") == []
    assert "couldn't find" in net.format_results("anything").lower()


def test_format_results(monkeypatch):
    monkeypatch.setattr(net, "_get", lambda url, **kw: _DDG_HTML)
    out = net.format_results("example")
    assert "Web results for 'example'" in out
    assert "https://example.com/page" in out


def test_fetch_page_returns_readable_text(monkeypatch):
    markup = "<html><body><h1>Title</h1><p>Body text here</p></body></html>"
    monkeypatch.setattr(net, "_get", lambda url, **kw: markup)
    out = net.fetch_page("example.com")
    assert "Title" in out and "Body text here" in out


def test_fetch_page_rejects_binary():
    assert "file" in net.fetch_page("https://x.com/a.pdf").lower()


def test_fetch_page_prefixes_scheme(monkeypatch):
    seen = {}
    monkeypatch.setattr(net, "_get", lambda url, **kw: seen.setdefault("url", url) or "ok")
    net.fetch_page("example.com")
    assert seen["url"].startswith("https://")


def test_wikipedia_uses_extract(monkeypatch):
    monkeypatch.setattr(net, "_get_json",
                        lambda url, **kw: {"extract": "Linux is a kernel."})
    assert net.wikipedia("Linux") == "Linux is a kernel."


def test_wikipedia_falls_back_to_search(monkeypatch):
    calls = {"n": 0}

    def fake_json(url, **kw):
        if "opensearch" in url:
            return ["linux", ["Linux kernel"], [], []]
        calls["n"] += 1
        return None if calls["n"] == 1 else {"extract": "The Linux kernel."}

    monkeypatch.setattr(net, "_get_json", fake_json)
    assert net.wikipedia("linux kernl") == "The Linux kernel."


def test_weather_returns_line(monkeypatch):
    monkeypatch.setattr(net, "_get", lambda url, **kw: "London: +13C")
    assert "London" in net.weather("London")


def test_failures_are_graceful(monkeypatch):
    monkeypatch.setattr(net, "_get", lambda url, **kw: None)
    monkeypatch.setattr(net, "_get_json", lambda url, **kw: None)
    assert "couldn't" in net.fetch_page("example.com").lower()
    assert "couldn't" in net.wikipedia("zzz").lower()
    assert "couldn't" in net.weather().lower()
