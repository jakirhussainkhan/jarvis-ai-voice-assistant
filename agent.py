"""Core Groq-backed agent orchestration for Jarvis.

Groq exposes an OpenAI-compatible Chat Completions API, so this uses the
official `openai` client pointed at the Groq base URL and runs its own bounded
tool-calling loop.
"""

import hashlib
import inspect
import json

from openai import OpenAI

from config import (
    API_KEY,
    BASE_URL,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
)
from tools import (
    fn_brightness,
    fn_close_app,
    fn_list_running,
    fn_lock_screen,
    fn_media,
    fn_open_app,
    fn_read_file,
    fn_save_note,
    fn_screenshot,
    fn_system_status,
    fn_volume,
    fn_web_search,
    fn_youtube,
)

SYSTEM_INSTRUCTION = (
    "You are Jarvis, a Linux voice assistant. Reply briefly -- one or two short "
    "sentences -- and always in English, regardless of the language the user "
    "writes in.\n"
    "When the user asks you to do something on the computer, call the matching "
    "tool and then confirm it in a few words; never just describe what you would "
    "do. Use fn_open_app/fn_close_app for apps, fn_youtube(action='play', "
    "query=<song name>) to play a song or video, fn_youtube(action='open') for "
    "the homepage, fn_media for pause/resume/next, fn_volume, fn_brightness, "
    "fn_screenshot, fn_lock_screen, fn_system_status, fn_web_search, "
    "fn_save_note and fn_read_file as appropriate.\n"
    "Never claim you did something you did not do."
)

# Python callables double as the tool declarations: each function's type hints
# become the JSON parameter types and its docstring becomes the description, so
# there is no hand-written schema to keep in sync (see _tool_schema).
_TOOLS = [
    fn_open_app,
    fn_close_app,
    fn_list_running,
    fn_youtube,
    fn_media,
    fn_volume,
    fn_brightness,
    fn_screenshot,
    fn_lock_screen,
    fn_system_status,
    fn_web_search,
    fn_save_note,
    fn_read_file,
]

_DISPATCH = {fn.__name__: fn for fn in _TOOLS}

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}

_client = OpenAI(api_key=API_KEY, base_url=BASE_URL) if API_KEY else None

# md5(query.lower()) -> reply
_CACHE = {}


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


def _chat(message):
    """Run one bounded tool-calling conversation. Returns (reply, tokens)."""
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": message},
    ]
    tokens = 0

    # Up to MAX_TOOL_ROUNDS tool rounds, then one final answer-only call.
    for _ in range(MAX_TOOL_ROUNDS + 1):
        response = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=_TOOL_SCHEMAS,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
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

    response = _client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    tokens += _tokens(response)
    return (response.choices[0].message.content or "").strip(), tokens


def _remember(memory, user_text, reply):
    if memory is None:
        return
    memory.add("user", user_text)
    memory.add("assistant", reply)


def run_agent(user_text, memory=None):
    """Handle one user turn. Returns (reply, tokens_used)."""
    query = (user_text or "").strip()
    key = _cache_key(query.lower())

    if key in _CACHE:
        return _CACHE[key], 0  # no memory write on cache hit

    history = memory.as_text() if memory is not None else ""
    message = f"{history}\nUser: {query}" if history else query

    reply = ""
    tokens = 0
    if _client is not None:
        reply, tokens = _chat(message)

    if not reply:
        reply = "Sorry, I couldn't finish that."

    _CACHE[key] = reply
    _remember(memory, query, reply)
    return reply, tokens
