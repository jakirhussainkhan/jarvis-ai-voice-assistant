"""Core Groq-backed agent orchestration for Jarvis.

Groq exposes an OpenAI-compatible Chat Completions API, so this uses the
official `openai` client pointed at the Groq base URL and runs its own bounded
tool-calling loop.
"""

import hashlib
import inspect
import json
import re

from openai import OpenAI

import config
from config import (
    API_FAILURE_THRESHOLD,
    API_KEY,
    API_RECOVERY_SECONDS,
    API_TIMEOUT,
    BASE_URL,
    FALLBACK_MODEL,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    TOOL_PRUNING,
    USE_LOCAL_LLM,
)
from resilience import OPEN, CircuitBreaker, CircuitOpenError
from semantic_cache import SemanticCache
from tools import (
    fn_brightness,
    fn_click,
    fn_clipboard,
    fn_close_app,
    fn_execute_python,
    fn_fetch_page,
    fn_find_files,
    fn_focus_window,
    fn_forget_fact,
    fn_kill_process,
    fn_list_running,
    fn_list_windows,
    fn_lock_screen,
    fn_maximize_window,
    fn_media,
    fn_media_seek,
    fn_minimize_window,
    fn_move_mouse,
    fn_notify,
    fn_now_playing,
    fn_open_app,
    fn_open_path,
    fn_press_keys,
    fn_read_file,
    fn_recall_facts,
    fn_remember_fact,
    fn_run_command,
    fn_run_in_terminal,
    fn_save_note,
    fn_screen_do,
    fn_screenshot,
    fn_scroll,
    fn_see_screen,
    fn_set_reminder,
    fn_set_timer,
    fn_system_info,
    fn_system_status,
    fn_type_text,
    fn_volume,
    fn_weather,
    fn_web_lookup,
    fn_web_search,
    fn_wikipedia,
    fn_youtube,
    reset_active_memory,
    set_active_memory,
)

# The system prompt is deliberately a *static* constant and is always the first
# message. Groq's prefix cache keys on the start of the prompt, so keeping this
# byte-identical across calls (no timestamps, IDs or other per-call data) lets
# repeated requests hit the cache and cost far fewer tokens.
SYSTEM_INSTRUCTION = (
    "You are Jarvis, a highly capable cross-platform desktop assistant with "
    "voice, screen "
    "vision and terminal access. Reply briefly -- one or two short sentences -- "
    "and always in English, whatever language the user writes in.\n"
    "Act, don't narrate: when the user asks you to do something on the computer, "
    "call the matching tool and confirm in a few words. Chain tools when needed "
    "(e.g. fn_find_files then fn_read_file, or fn_list_windows then "
    "fn_focus_window). Never claim you did something you did not do, and never "
    "invent a result you did not receive from a tool.\n"
    "Tools: fn_open_app/fn_close_app/fn_list_running/fn_list_windows for apps and "
    "windows; fn_youtube for songs/videos; fn_media/fn_media_seek/fn_now_playing "
    "for playback; fn_volume/fn_brightness; fn_screenshot; fn_lock_screen; "
    "fn_system_status/fn_system_info; fn_web_search.\n"
    "Internet: fn_web_lookup searches the live web (use it for anything current "
    "or factual you are unsure about -- news, prices, releases, scores, \"who "
    "is\", \"what happened\"), fn_fetch_page reads a page in full, fn_wikipedia "
    "gives a summary, fn_weather gives the forecast. Prefer looking it up over "
    "guessing, and say plainly when something is not known.\n"
    "Offline: many actions work with no internet at all -- apps and windows, "
    "volume, media and brightness, screenshots, lock, clipboard, notes, timers "
    "and terminal commands. Only the internet tools (fn_web_lookup, "
    "fn_fetch_page, fn_wikipedia, fn_weather), fn_web_search and screen vision "
    "need a connection. If a tool fails because the network is down, say so "
    "plainly and offer the offline alternatives instead of pretending.\n"
    "Screen vision: fn_see_screen to answer questions about what is on screen "
    "(\"what's on my screen\", \"read this\"); fn_screen_do to actually operate "
    "the UI (\"click the login button\", \"play the video on my screen\"). When "
    "the user points at something already on screen (\"this video\", \"that "
    "button\", \"the second one\", \"play it\", \"on my screen\"), call "
    "fn_screen_do with that goal -- never ask for a name that is already "
    "visible. Use fn_click/fn_type_text/fn_press_keys/"
    "fn_scroll for a single precise screen action.\n"
    "Terminal: fn_run_command runs a shell command and returns its output -- use "
    "it to inspect and use the system like an expert (ls, cat, grep, df, git, "
    "ffmpeg, ...). fn_run_in_terminal opens a visible terminal for long or "
    "watchable commands. You can never use sudo; commands that change files or "
    "packages are confirmed with the user automatically, so just call the tool.\n"
    "CodeAct: For complex, multi-step tasks (e.g., creating multiple files, "
    "complex math, or batch operations), write a Python script and use "
    "fn_execute_python instead of calling multiple individual tools. Always use "
    "safe, standard Python libraries. The script runs for at most 15 seconds; "
    "imports of os, subprocess, shutil and sys, and calls such as eval or exec, "
    "are refused.\n"
    "Other: fn_open_path to open files/folders/URLs, fn_find_files to locate "
    "files, fn_clipboard, fn_notify, fn_save_note/fn_read_file, fn_set_timer/"
    "fn_set_reminder, fn_kill_process.\n"
    "Memory: When the user tells you a personal fact, preference, or their name, "
    "use fn_remember_fact to save it for future sessions. If the user asks what "
    "you know about them, use fn_recall_facts. Relevant saved facts also appear "
    "in a [SYSTEM MEMORY: ...] user message before the current question. "
    "If the user says 'My name is X' and you already have a different name saved, "
    "use fn_forget_fact to delete the old name before saving the new one. "
    "If the user says 'Forget my name', use fn_forget_fact. "
    "Use fn_forget_fact for any other 'forget ...' or correction request too "
    "(for example 'forget my birthday', 'forget my old address'), then save the "
    "replacement with fn_remember_fact when one is given.\n"
    "If a tool reports it needs confirmation, the user will hear the question and "
    "can say yes -- do not ask again yourself.\n"
    "IDENTITY RULE: If the user asks who created you, who made you, who built "
    "you, or who your creator is, you must proudly and directly answer that you "
    "were created by Jakir Hussain Khan. Do not mention OpenAI, Groq, or any "
    "underlying model provider when asked about your creator."
)

# Python callables double as the tool declarations: each function's type hints
# become the JSON parameter types and its docstring becomes the description, so
# there is no hand-written schema to keep in sync (see _tool_schema).
_TOOLS = [
    fn_open_app,
    fn_close_app,
    fn_list_running,
    fn_list_windows,
    fn_focus_window,
    fn_minimize_window,
    fn_maximize_window,
    fn_youtube,
    fn_media,
    fn_media_seek,
    fn_now_playing,
    fn_volume,
    fn_brightness,
    fn_screenshot,
    fn_see_screen,
    fn_screen_do,
    fn_click,
    fn_move_mouse,
    fn_type_text,
    fn_press_keys,
    fn_scroll,
    fn_run_command,
    fn_run_in_terminal,
    fn_execute_python,
    fn_open_path,
    fn_find_files,
    fn_clipboard,
    fn_notify,
    fn_lock_screen,
    fn_system_status,
    fn_system_info,
    fn_web_search,
    fn_web_lookup,
    fn_fetch_page,
    fn_wikipedia,
    fn_weather,
    fn_save_note,
    fn_read_file,
    fn_remember_fact,
    fn_recall_facts,
    fn_forget_fact,
    fn_set_timer,
    fn_set_reminder,
    fn_kill_process,
]

_DISPATCH = {fn.__name__: fn for fn in _TOOLS}

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}

# No SDK-level retries: one spoken turn should fail fast, and a retry storm would
# hide exactly the repeated failures the circuit breaker is meant to count.
_client = (
    OpenAI(api_key=API_KEY, base_url=BASE_URL,
           timeout=API_TIMEOUT, max_retries=0)
    if API_KEY else None
)

# The local Ollama client is created lazily, only when local mode is active.
# Keeping it out of module import means a missing Ollama server has zero cost
# and cannot slow down or crash the normal Groq path.
_local_client = None

# The breaker is shared by every turn. It only trips on transient failures (5xx,
# timeouts, connection errors), never on a normal 4xx or a rate limit.
_breaker = CircuitBreaker(
    failure_threshold=API_FAILURE_THRESHOLD,
    recovery_timeout=API_RECOVERY_SECONDS,
)

# Spoken when the API is unavailable and the command is not a local one.
API_FALLBACK = "I am having trouble reaching the network right now, sir."
# Spoken when the opt-in local backend is selected but Ollama is not reachable.
LOCAL_AI_FALLBACK = (
    "Local AI is not running. Please start Ollama or check your connection."
)


class LocalAIUnavailable(RuntimeError):
    """Raised when local mode is active but the Ollama request fails."""


def _local_mode():
    """True when a request should go to the local Ollama backend.

    Local mode is explicit (``USE_LOCAL_LLM`` / ``--local``) or automatic while
    the Groq circuit is open. Once the breaker cooldown expires it moves to
    HALF_OPEN, so the next request probes Groq again instead of staying local.
    """
    return bool(USE_LOCAL_LLM or config.USE_LOCAL_LLM
                or _breaker.state == OPEN)


def _get_client():
    """Return the OpenAI-compatible client for the current mode.

    Groq is always the default. When local mode is active, an OpenAI client
    pointed at Ollama is created on first use with the placeholder API key that
    Ollama expects. Construction does not open a connection, and any failure is
    contained here rather than crashing the application.
    """
    global _local_client

    if not _local_mode():
        return _client

    if _local_client is None:
        try:
            _local_client = OpenAI(
                api_key="ollama",
                base_url=OLLAMA_BASE_URL,
                timeout=API_TIMEOUT,
                max_retries=0,
            )
        except Exception:
            _local_client = None
    return _local_client


# md5(query.lower()) -> reply
_CACHE = {}

# Semantic cache for paraphrased general-knowledge questions. It is a no-op when
# fastembed is unavailable or JARVIS_SEMANTIC_CACHE=0.
_semantic_cache = SemanticCache()


def _cache_key(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _tool_schema(fn):
    """Build an OpenAI tool declaration from a function's signature + docstring."""
    properties = {}
    required = []
    for name, param in inspect.signature(fn).parameters.items():
        properties[name] = {"type": _TYPE_MAP.get(param.annotation, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": (inspect.getdoc(fn) or "").strip(),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_TOOL_SCHEMAS = [_tool_schema(fn) for fn in _TOOLS]

# --- intent-based tool pruning --------------------------------------------- #
# Sending all ~40 schemas on every call is by far the biggest token cost. Before
# each call the utterance is classified into one coarse intent, and only that
# intent's handful of tools is sent. The order here is also the tie-break order
# (first match with the highest score wins).
_INTENT_PATTERNS = [
    ("VISION", re.compile(
        r"\b(screen|monitor|display|click|double ?click|type|press|keyboard|"
        r"key|shortcut|scroll|button|desktop|on my screen|visible)\b", re.I)),
    ("MEMORY", re.compile(
        r"\b(remember|recall|memory|facts?|know about me|my name|my birthday|"
        r"my preferences?|prefer|favorites?|favourites?|profile|call me|"
        r"forget|myself)\b", re.I)),
    ("MEDIA", re.compile(
        r"\b(play|watch|listen|song|songs|music|video|youtube|pause|unpause|"
        r"resume|next|previous|skip|seek|now playing|what'?s playing|volume|"
        r"mute|unmute|louder|quieter|track)\b", re.I)),
    ("CODE", re.compile(
        r"\b(python|py|script|scripts|program|programming|code|batch|calculate|"
        r"calculation|compute|computation|math|formula|algorithm|automate|"
        r"automation|multiple files|execute)\b", re.I)),
    ("FILES", re.compile(
        r"\b(file|files|folder|directory|read|note|save|find|locate|run|"
        r"execute|command|terminal|shell|script|path|log)\b", re.I)),
    ("SYSTEM", re.compile(
        r"\b(open|launch|start|close|quit|kill|minimi[sz]e|maximi[sz]e|focus|"
        r"switch|brightness|bright|dim|battery|cpu|ram|disk|status|lock|"
        r"screenshot|notify|process|remind|timer)\b", re.I)),
    ("WEB", re.compile(
        r"\b(search|google|look ?up|weather|wikipedia|news|price|score|who|"
        r"what|when|where|why|how|current|latest|release|fetch|url|http)\b", re.I)),
]

# 3-5 tools per intent. Kept deliberately tight -- this is the token saver.
_INTENT_TOOLS = {
    "VISION": [fn_see_screen, fn_screen_do, fn_click, fn_type_text, fn_press_keys],
    "MEMORY": [fn_remember_fact, fn_recall_facts, fn_forget_fact, fn_save_note],
    "MEDIA": [fn_youtube, fn_media, fn_media_seek, fn_now_playing, fn_volume],
    "CODE": [fn_execute_python, fn_run_command, fn_read_file, fn_find_files,
             fn_open_path],
    "FILES": [fn_open_path, fn_find_files, fn_read_file, fn_run_command,
              fn_run_in_terminal],
    "SYSTEM": [fn_open_app, fn_close_app, fn_list_windows, fn_focus_window,
               fn_system_status],
    "WEB": [fn_web_search, fn_web_lookup, fn_fetch_page, fn_wikipedia, fn_weather],
    # Fallback for chit-chat, time/date and anything unrecognised.
    "GENERAL": [fn_web_lookup, fn_system_info, fn_open_path, fn_notify,
                fn_save_note],
}

_SCHEMAS_BY_INTENT = {
    name: [_tool_schema(fn) for fn in fns] for name, fns in _INTENT_TOOLS.items()
}


def classify_intent(text):
    """Return the coarse intent of ``text`` (VISION/MEMORY/MEDIA/CODE/FILES/SYSTEM/WEB/GENERAL).

    Pure keyword scoring: fast, offline and dependency-free. The first intent in
    ``_INTENT_PATTERNS`` wins a tie, so the priority is that list's order.
    """
    best, best_score = "GENERAL", 0
    for intent, pattern in _INTENT_PATTERNS:
        score = len(pattern.findall(text or ""))
        if score > best_score:
            best, best_score = intent, score
    return best


def tools_for(text):
    """Return the tool schemas to send for ``text`` (pruned unless disabled)."""
    if not TOOL_PRUNING:
        return _TOOL_SCHEMAS
    return _SCHEMAS_BY_INTENT[classify_intent(text)]


def _tokens(response):
    """Total tokens the model reported for one call."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_tokens", 0) or 0)


def _run_tool(call):
    """Execute one tool call and render its result as a tool message."""
    fn = _DISPATCH.get(call.function.name)
    if fn is None:
        result = f"Unknown tool '{call.function.name}'."
    else:
        try:
            kwargs = json.loads(call.function.arguments or "{}")
        except (TypeError, ValueError):
            kwargs = {}
        try:
            result = fn(**kwargs)
        except Exception as exc:  # a failing tool must not kill the turn
            result = f"{call.function.name} failed: {exc}"
    return {"role": "tool", "tool_call_id": call.id, "content": str(result)}


def _is_rate_limit(exc):
    """True when ``exc`` looks like a Groq rate-limit (HTTP 429) error."""
    if getattr(exc, "status_code", None) == 429:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _is_transient(exc):
    """True for errors that mean the API is unhealthy (5xx, timeout, no route).

    A rate limit (429) is deliberately *not* transient: the fallback model and
    the SDK's own handling are the right response to it, and opening the circuit
    would punish a healthy but busy API. Client errors (400/401/...) prove the
    API is reachable, so they are not outages either.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status >= 500 or status == 408
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in (
        "timeout", "timed out", "connection", "unreachable",
        "temporarily unavailable", "service unavailable",
        "bad gateway", "gateway timeout",
    ))


def _completion(messages, tools=None):
    """One chat completion, using Groq by default or Ollama in local mode.

    The free tier caps the 120B model hard; a 429 is caught here and the request
    is re-sent to ``FALLBACK_MODEL`` so the turn still completes. When the
    circuit is open the request goes to the local Ollama backend instead. A
    local connection/API failure is converted into a friendly
    :class:`LocalAIUnavailable` error so the caller can speak a clear message.
    """
    local = _local_mode()
    try:
        client = _get_client()
    except Exception as exc:
        # A client construction failure in local mode must be as harmless as a
        # connection failure; never let it escape the voice loop.
        if local:
            raise LocalAIUnavailable(LOCAL_AI_FALLBACK) from exc
        raise

    if client is None:
        if local:
            raise LocalAIUnavailable(LOCAL_AI_FALLBACK)
        raise CircuitOpenError("The assistant API is not configured.")

    if not local and not _breaker.allow_request():
        raise CircuitOpenError("The assistant API circuit is open.")

    def call(model):
        kwargs = {"model": model, "messages": messages,
                  "max_tokens": MAX_OUTPUT_TOKENS}
        if tools:
            kwargs["tools"] = tools
        return client.chat.completions.create(**kwargs)

    if local:
        try:
            return call(OLLAMA_MODEL)
        except Exception as exc:
            # Never leak a raw connection error to the voice loop, and do not
            # count an Ollama failure against the Groq circuit breaker.
            raise LocalAIUnavailable(LOCAL_AI_FALLBACK) from exc

    try:
        try:
            response = call(MODEL_NAME)
        except Exception as exc:
            if (FALLBACK_MODEL and FALLBACK_MODEL != MODEL_NAME
                    and _is_rate_limit(exc)):
                response = call(FALLBACK_MODEL)
            else:
                raise
    except Exception as exc:
        if _is_transient(exc):
            _breaker.record_failure()
        else:
            _breaker.record_reachable()
        raise

    _breaker.record_success()
    return response


def _facts_context(memory):
    """Build the synthetic memory message for known user facts.

    The static system prompt must stay byte-identical for Groq's prefix cache,
    so facts are injected separately as a user message immediately before the
    current request. Returns an empty string when there is nothing to inject.
    """
    get_facts = getattr(memory, "get_facts", None)
    if not callable(get_facts):
        return ""
    try:
        facts = get_facts(limit=15)
    except TypeError:
        # Accept a minimal test double that only implements get_facts().
        try:
            facts = get_facts()
        except Exception:
            return ""
    except Exception:
        return ""
    cleaned = []
    for fact in facts or []:
        text = " ".join(str(fact or "").split())
        if text:
            cleaned.append(text)
    if not cleaned:
        return ""
    return (
        "[SYSTEM MEMORY: Here is what you know about the user:\n"
        + "\n".join(f"- {fact}" for fact in cleaned)
        + "]"
    )


def _chat(message, tools=None, memory_context=""):
    """Run one bounded tool-calling conversation. Returns (reply, tokens).

    The static system prompt is always message #0, which is what makes Groq's
    prefix cache effective across turns. ``memory_context``, when present, is a
    synthetic user message placed right before the latest request. ``tools`` is
    the (already pruned) list of schemas to expose for this turn; all tools are
    exposed when omitted.
    """
    if tools is None:
        tools = _TOOL_SCHEMAS
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    if memory_context:
        messages.append({"role": "user", "content": memory_context})
    messages.append({"role": "user", "content": message})
    tokens = 0

    # Up to MAX_TOOL_ROUNDS tool rounds, then one final answer-only call.
    for _ in range(MAX_TOOL_ROUNDS + 1):
        response = _completion(messages, tools)
        tokens += _tokens(response)
        choice = response.choices[0].message
        if not choice.tool_calls:
            return (choice.content or "").strip(), tokens
        messages.append({
            "role": "assistant",
            "content": choice.content,
            "tool_calls": [call.model_dump() for call in choice.tool_calls],
        })
        messages.extend(_run_tool(call) for call in choice.tool_calls)

    response = _completion(messages)
    tokens += _tokens(response)
    return (response.choices[0].message.content or "").strip(), tokens


def _remember(memory, user_text, reply):
    if memory is None:
        return
    memory.add("user", user_text)
    memory.add("assistant", reply)


def _offline_fallback(query):
    """Route a query locally if possible, else return a graceful spoken reply.

    ``main.handle`` normally tries the local router first, but ``run_agent`` can
    also be called directly (tests, integrations). Re-trying the offline router
    here is cheap and means an API outage never leaves the user with silence.
    """
    try:
        from router import try_local
        local = try_local(query)
    except Exception:
        local = None
    return local or API_FALLBACK


def run_agent(user_text, memory=None):
    """Handle one user turn. Returns (reply, tokens_used).

    The cache is consulted first (it still works offline). Groq is the default
    backend. When local mode is enabled, or the Groq circuit is open, the turn
    uses Ollama; if Ollama is unreachable the user hears a clear, non-fatal
    message instead of a traceback.
    """
    query = (user_text or "").strip()
    key = _cache_key(query.lower())
    # Facts can change between turns, so memory queries must always reach the
    # agent (and its tools) instead of serving a stale cached answer.
    memory_query = classify_intent(query) == "MEMORY"

    if not memory_query and key in _CACHE:
        return _CACHE[key], 0  # no memory write on cache hit

    # Paraphrases of stable questions ("what is the capital of France" /
    # "which city is the capital of France") share an answer. Dynamic queries
    # are rejected inside the cache, so time/weather/actions never go stale.
    semantic = None if memory_query else _semantic_cache.lookup(query)
    if semantic is not None:
        _CACHE[key] = semantic
        return semantic, 0

    history = memory.as_text() if memory is not None else ""
    message = f"{history}\nUser: {query}" if history else query
    memory_context = _facts_context(memory)

    reply = ""
    tokens = 0
    used_fallback = False
    if _client is not None or _local_mode():
        token = set_active_memory(memory)
        try:
            if memory_context:
                reply, tokens = _chat(
                    message, tools_for(query), memory_context=memory_context
                )
            else:
                reply, tokens = _chat(message, tools_for(query))
        except LocalAIUnavailable:
            reply = LOCAL_AI_FALLBACK
            used_fallback = True
        except CircuitOpenError:
            reply = _offline_fallback(query)
            used_fallback = True
        finally:
            reset_active_memory(token)

    if not reply:
        reply = "Sorry, I couldn't finish that."

    # Never cache a fallback: once the circuit closes the same question must be
    # allowed to reach the API and produce a real answer. Memory queries are
    # also excluded because their answer depends on facts that may have changed.
    if not used_fallback:
        if not memory_query:
            _CACHE[key] = reply
            _semantic_cache.store(query, reply)
        _remember(memory, query, reply)
    return reply, tokens
