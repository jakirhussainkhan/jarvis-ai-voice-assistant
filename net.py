"""Live internet access for Jarvis: web search, page fetch, Wikipedia, weather.

This is what lets the model reason over *fresh* information instead of only its
training data. Everything uses the standard library (``urllib``) with timeouts
and never raises -- a site being down just returns an empty/None result.

No API keys are required: DuckDuckGo (HTML), Wikipedia's REST API and wttr.in are
all keyless.
"""

import html as _html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import logger
from digest import DIGEST_THRESHOLD_CHARS, digest_text

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124 Safari/537.36")

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def _get(url, timeout=10, max_bytes=600_000, ua=None):
    """Fetch a URL and return decoded text, or None on any failure."""
    request = urllib.request.Request(url, headers={
        "User-Agent": ua or _UA,
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
    except Exception as exc:
        logger.log_api_error(f"Network request failed for {url}", exc)
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _get_json(url, timeout=10):
    text = _get(url, timeout=timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        # HTTP 200 with a non-JSON body (a block page, an HTML error page, a
        # truncated response) is an API failure worth recording.
        logger.log_api_error(f"Invalid JSON response from {url}", exc)
        return None


def html_to_text(markup, limit=None):
    """Strip tags/scripts from HTML and unescape entities into readable text."""
    if not markup:
        return ""
    text = _SCRIPT_RE.sub(" ", markup)
    # Turn block-level tags into newlines so paragraphs survive.
    text = re.sub(r"(?i)</?(p|div|br|li|tr|h[1-6]|section|article)[^>]*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKS_RE.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + " …[truncated]"
    return text


# --------------------------------------------------------------------------- #
# search (DuckDuckGo HTML, keyless)
# --------------------------------------------------------------------------- #

_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S | re.I,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(?P<snip>.*?)</a>', re.S | re.I
)
_LITE_RE = re.compile(
    r'<a[^>]*rel="nofollow"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S | re.I,
)


def _real_url(href):
    """Resolve DuckDuckGo's /l/?uddg= redirect wrapper to the real URL."""
    if "uddg=" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return "https:" + href
    return href


def search(query, limit=5):
    """Return ``[{title, url, snippet}]`` for a web search, or ``[]``."""
    query = (query or "").strip()
    if not query:
        return []
    encoded = urllib.parse.quote_plus(query)
    markup = _get(f"https://html.duckduckgo.com/html/?q={encoded}")
    results = []
    if markup:
        titles = list(_RESULT_RE.finditer(markup))
        snippets = [m.group("snip") for m in _SNIPPET_RE.finditer(markup)]
        for i, match in enumerate(titles[:limit]):
            snippet = snippets[i] if i < len(snippets) else ""
            results.append({
                "title": html_to_text(match.group("title")),
                "url": _real_url(match.group("href")),
                "snippet": html_to_text(snippet),
            })
    if not results:
        markup = _get(f"https://lite.duckduckgo.com/lite/?q={encoded}")
        for match in list(_LITE_RE.finditer(markup or ""))[:limit]:
            results.append({
                "title": html_to_text(match.group("title")),
                "url": _real_url(match.group("href")),
                "snippet": "",
            })
    return results


def format_results(query, limit=5):
    """Render :func:`search` as a compact text block for the model."""
    results = search(query, limit=limit)
    if not results:
        return f"I couldn't find anything for '{query}'."
    lines = [f"Web results for '{query}':"]
    for i, item in enumerate(results, 1):
        line = f"{i}. {item['title']} — {item['url']}"
        if item["snippet"]:
            line += f"\n   {item['snippet']}"
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# fetch a page / Wikipedia / weather
# --------------------------------------------------------------------------- #

def fetch_page(url, limit=4000):
    """Fetch a URL and return readable text, or a digest for very large pages.

    Pages below the digest threshold keep the old capped behaviour. Large pages
    are condensed by the cheap sub-agent so one article cannot flood the main
    model's context window with thousands of tokens.
    """
    url = (url or "").strip()
    if not url:
        return "Which URL should I read?"
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if re.search(r"\.(png|jpe?g|gif|webp|svg|mp4|mp3|zip|gz|pdf|iso)(\?|$)", url, re.I):
        return "That looks like a file, not a web page."
    markup = _get(url)
    if markup is None:
        return f"I couldn't reach {url}."
    text = html_to_text(markup)
    if len(text) >= DIGEST_THRESHOLD_CHARS:
        summary = digest_text(
            text,
            prompt=f"Summarize the key facts from the web page at {url}.",
        )
        return summary or f"{url} had no readable text."
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + " …[truncated]"
    return text or f"{url} had no readable text."


def wikipedia(query, limit=1200):
    """Return the Wikipedia summary of ``query``, or an error string."""
    query = (query or "").strip()
    if not query:
        return "What should I look up on Wikipedia?"
    title = urllib.parse.quote(query.replace(" ", "_"))
    data = _get_json(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=10
    )
    extract = (data or {}).get("extract")
    if not extract:
        # Fall back to Wikipedia's own search to fix the title.
        search_url = ("https://en.wikipedia.org/w/api.php?action=opensearch&"
                      "format=json&limit=1&search=" + urllib.parse.quote(query))
        found = _get_json(search_url)
        if found and len(found) > 1 and found[1]:
            title = urllib.parse.quote(found[1][0].replace(" ", "_"))
            data = _get_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                timeout=10,
            )
            extract = (data or {}).get("extract")
    if not extract:
        return f"I couldn't find a Wikipedia article for '{query}'."
    return extract[:limit].rstrip()


def weather(city="", limit=400):
    """Return a one-line weather report via wttr.in, or an error string."""
    city = (city or "").strip()
    location = urllib.parse.quote_plus(city) if city else ""
    fmt = "Weather+in+%l:+%c+%t,+wind+%w,+humidity+%h"
    # wttr.in serves an HTML page to browser-like agents; plain text to curl.
    text = _get(f"https://wttr.in/{location}?format={fmt}", timeout=10, ua="curl/8.0")
    if not text or _looks_like_html(text):
        # Fall back to the simplest format wttr.in supports.
        text = _get(f"https://wttr.in/{location}?format=3", timeout=10, ua="curl/8.0")
    if not text or _looks_like_html(text):
        return "I couldn't get the weather right now."
    return text.strip()[:limit]


def _looks_like_html(text):
    low = (text or "").lower()
    return "<html" in low or "<!doctype" in low or "<head" in low
