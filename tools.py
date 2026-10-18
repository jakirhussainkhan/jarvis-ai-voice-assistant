"""Tool definitions Jarvis can invoke on the local Linux system.

Each tool is a plain Python function with type hints and a docstring. The agent
reads that signature to build the OpenAI-compatible tool declaration, so there
is no hand-written TOOL_SCHEMA or DISPATCH table to maintain.
"""

import os
import shutil
import subprocess
import webbrowser
from datetime import datetime

import psutil

_NOTES_PATH = os.path.expanduser("~/jarvis_notes.md")
_READ_LIMIT = 1500
_DEVNULL = subprocess.DEVNULL

APP_COMMANDS = {
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"],
    "firefox": ["firefox"],
    "code": ["code", "codium", "vscodium", "code-insiders"],
    "terminal": ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
                 "alacritty", "kitty", "xterm"],
    "files": ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm", "xdg-open"],
    "calculator": ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "xcalc"],
    "vlc": ["vlc"],
}

APP_ALIASES = {
    "google-chrome": "chrome",
    "chromium": "chrome",
    "browser": "chrome",
    "vs code": "code",
    "vscode": "code",
    "editor": "code",
    "term": "terminal",
    "console": "terminal",
    "shell": "terminal",
    "file manager": "files",
    "nautilus": "files",
    "calc": "calculator",
    "video": "vlc",
    "player": "vlc",
}


# --------------------------------------------------------------------------- #
# tools - each returns a short string for the model
# --------------------------------------------------------------------------- #

def fn_open_app(name: str) -> str:
    """Launch a desktop application by name."""
    key = APP_ALIASES.get(name.strip().lower(), name.strip().lower())
    candidates = APP_COMMANDS.get(key)
    if candidates is None:
        known = ", ".join(sorted(APP_COMMANDS))
        return f"Unknown app '{name}'. Known apps: {known}."

    for cmd in candidates:
        if not shutil.which(cmd):
            continue
        args = [cmd, os.path.expanduser("~")] if cmd == "xdg-open" else [cmd]
        try:
            subprocess.Popen(
                args,
                stdin=_DEVNULL,
                stdout=_DEVNULL,
                stderr=_DEVNULL,
                start_new_session=True,
            )
            return f"Opened {key}."
        except OSError:
            continue
    return f"{key} is not installed."


def fn_system_status() -> str:
    """Report current CPU, RAM and disk usage."""
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    return f"CPU {cpu:.0f}%, RAM {ram:.0f}%, Disk {disk:.0f}%"


def fn_youtube(query: str) -> str:
    """Open a YouTube search in the default browser."""
    url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
    try:
        webbrowser.open(url)
    except Exception as exc:
        return f"Could not open YouTube: {exc}"
    return f"Searching YouTube for '{query}'."


def fn_web_search(query: str) -> str:
    """Open a Google search in the default browser."""
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    try:
        webbrowser.open(url)
    except Exception as exc:
        return f"Could not search: {exc}"
    return f"Searching Google for '{query}'."


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
