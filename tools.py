"""Tool definitions Jarvis can invoke on the local Linux system.

Each tool is a plain Python function with type hints and a docstring. The agent
reads that signature to build the OpenAI-compatible tool declaration, so there
is no hand-written TOOL_SCHEMA or DISPATCH table to maintain.

All system actions go through :mod:`desktop`, which only ever runs whitelisted
commands (never ``sudo``, never arbitrary shell). Destructive power actions are
not exposed here at all -- they are handled by the router through
:mod:`confirm`, so a stray utterance can never shut the machine down.
"""

import os
from datetime import datetime

import psutil

import confirm
import desktop

_NOTES_PATH = os.path.expanduser("~/jarvis_notes.md")
_READ_LIMIT = 1500


# --------------------------------------------------------------------------- #
# tools - each returns a short string for the model
# --------------------------------------------------------------------------- #

def fn_open_app(name: str) -> str:
    """Open an installed desktop application by (fuzzy) name."""
    return desktop.open_app(name)


def fn_close_app(name: str) -> str:
    """Close a running application by name. Protected apps are never closed.

    A weak (misheard) match is not closed directly; the user is asked to confirm
    with "Did you mean <app>?" first.
    """
    app, score = desktop.match_app(name)
    if app is None or score < desktop.WEAK_MATCH:
        return f"I couldn't find an app called {name}."
    if score < desktop.STRONG_MATCH:
        return confirm.request(
            lambda a=app: desktop.close_app(a), f"Did you mean {app.name}?"
        )
    return desktop.close_app(app)


def fn_list_running() -> str:
    """List the application windows / processes that are currently open."""
    return desktop.list_running()


def fn_youtube(action: str, query: str = "") -> str:
    """Control YouTube: action is 'open', 'play' or 'search' (query optional)."""
    action = (action or "").strip().lower()
    if action == "open":
        return desktop.open_youtube()
    if action == "play":
        return desktop.play_youtube(query)
    if action == "search":
        return desktop.search_youtube(query)
    return "Use action 'open', 'play' or 'search'."


def fn_media(action: str) -> str:
    """Control media playback: pause, resume, next, previous or stop."""
    return desktop.media_control(action)


def fn_volume(action: str, value: int = -1) -> str:
    """Control volume: up, down, mute, unmute, or set to `value` percent (0-100)."""
    action = (action or "").strip().lower()
    if value is not None and value >= 0:
        return desktop.volume_control("set", value)
    return desktop.volume_control(action)


def fn_brightness(action: str, value: int = -1) -> str:
    """Control screen brightness: up, down, or set to `value` percent (1-100)."""
    action = (action or "").strip().lower()
    if value is not None and value >= 0:
        return desktop.brightness_control("set", value)
    return desktop.brightness_control(action)


def fn_screenshot() -> str:
    """Take a screenshot and save it in ~/Pictures."""
    return desktop.screenshot()


def fn_lock_screen() -> str:
    """Lock the desktop session."""
    return desktop.lock_screen()


def fn_system_status() -> str:
    """Report current CPU, RAM and disk usage."""
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    return f"CPU {cpu:.0f}%, RAM {ram:.0f}%, Disk {disk:.0f}%"


def fn_web_search(query: str) -> str:
    """Open a Google search in the default browser."""
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    if desktop._open_url(url):
        return f"Searching Google for '{query}'."
    return "Could not open the browser."


def fn_save_note(text: str) -> str:
    """Append a timestamped note to ~/jarvis_notes.md."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_NOTES_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"- [{stamp}] {text}\n")
    except OSError as exc:
        return f"Could not save note: {exc}"
    return f"Note saved to {_NOTES_PATH}."


def fn_read_file(path: str) -> str:
    """Return the first 1500 characters of a text file."""
    full = os.path.expanduser(path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(_READ_LIMIT + 1)
    except FileNotFoundError:
        return f"File not found: {path}"
    except IsADirectoryError:
        return f"Not a file: {path}"
    except OSError as exc:
        return f"Could not read {path}: {exc}"

    if len(content) > _READ_LIMIT:
        return content[:_READ_LIMIT] + "\n...[truncated]"
    return content or f"{path} is empty."
