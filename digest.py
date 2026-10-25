"""Cheap sub-agent digests for large tool outputs.

Vision answers and fetched pages can easily be thousands of tokens. Sending all
of that to the main agent is slow and expensive, so large results are condensed
first. A small/cheap model is tried when an API key is configured; otherwise a
deterministic local extractive summary is used, so the helper never fails or
requires the network.
"""

import re

from openai import OpenAI

import logger
from config import API_KEY, BASE_URL, FALLBACK_MODEL, MODEL_NAME

# ~3000 tokens of English text. Results below this are returned unchanged.
DIGEST_THRESHOLD_CHARS = 12_000
# Keep digests around 50-100 tokens; long enough for names/numbers, short enough
# to protect the main agent's context window.
DIGEST_MAX_CHARS = 400
_DIGEST_INPUT_CHARS = 40_000

_DIGEST_MODEL = FALLBACK_MODEL or MODEL_NAME
_client = (
    OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=20, max_retries=0)
    if API_KEY else None
)

_SYSTEM = (
    "You are a concise research sub-agent. Condense the supplied text to at most "
    "50 words. Preserve names, numbers, dates, and the main conclusion. Reply in "
    "English only, with no preamble or markdown headings."
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def is_large(text, threshold=DIGEST_THRESHOLD_CHARS):
    """True when ``text`` is large enough to be worth digesting."""
    return len(str(text or "")) >= threshold


def _local_digest(text):
    """Deterministic extractive fallback: the first sentences up to the cap."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    if len(clean) <= DIGEST_MAX_CHARS:
        return clean

    picked = []
    size = 0
    for sentence in _SENTENCE_RE.split(clean):
        sentence = sentence.strip()
        if not sentence:
            continue
        if size + len(sentence) + 1 > DIGEST_MAX_CHARS:
            break
        picked.append(sentence)
        size += len(sentence) + 1
    summary = " ".join(picked).strip()
    if not summary:
        summary = clean[:DIGEST_MAX_CHARS].rstrip()
    return summary + " …"


def _llm_digest(text, prompt):
    """Ask the cheap model for a digest. Returns None when unavailable."""
    if _client is None:
        return None
    try:
        resp = _client.chat.completions.create(
            model=_DIGEST_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": (
                    f"{prompt or 'Summarize the key facts.'}\n\n"
                    f"Text:\n{text[:_DIGEST_INPUT_CHARS]}"
                )},
            ],
            max_tokens=120,
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        # The local extractive digest is the fallback, but the API failure is
        # still worth recording for debugging.
        logger.log_api_error("Digest API request failed", exc)
        return None
    return content[:DIGEST_MAX_CHARS].rstrip() or None


def digest_text(text, prompt="Summarize the key facts."):
    """Condense ``text`` when it is large; otherwise return it unchanged.

    The function is best-effort: model failures fall back to the local summary,
    and short strings are returned verbatim so callers can use it unconditionally.
    """
    text = "" if text is None else str(text)
    if len(text) < DIGEST_THRESHOLD_CHARS:
        return text
    return _llm_digest(text, prompt) or _local_digest(text)
