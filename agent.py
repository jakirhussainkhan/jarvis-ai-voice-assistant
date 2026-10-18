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
    fn_click,
    fn_clipboard,
    fn_close_app,
    fn_fetch_page,
    fn_find_files,
    fn_focus_window,
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
)

SYSTEM_INSTRUCTION = (
    "You are Jarvis, a highly capable Linux desktop assistant with voice, screen "
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
    "Other: fn_open_path to open files/folders/URLs, fn_find_files to locate "
    "files, fn_clipboard, fn_notify, fn_save_note/fn_read_file, fn_set_timer/"
    "fn_set_reminder, fn_kill_process.\n"
    "If a tool reports it needs confirmation, the user will hear the question and "
    "can say yes -- do not ask again yourself."
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
    fn_set_timer,
    fn_set_reminder,
    fn_kill_process,
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
