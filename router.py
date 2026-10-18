"""Routes user input to the appropriate agent capability or tool.

`try_local` handles a small set of commands entirely offline (regex only, no
API calls). It returns a string with the result, or None when nothing matches
so the agent can decide what to do.
"""

import os
import re
import shutil
import subprocess
import webbrowser
from datetime import datetime

import psutil

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_DEVNULL = subprocess.DEVNULL


def _launch(candidates):
    """Start the first available program, detached, with output discarded."""
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
            return True
        except OSError:
            continue
    return False


def _run(args):
    """Fire a best-effort system command with output discarded."""
    if not shutil.which(args[0]):
        return False
    try:
        subprocess.Popen(
            args,
            stdin=_DEVNULL,
            stdout=_DEVNULL,
            stderr=_DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def _open(url):
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _close_app(candidates):
    """Best-effort graceful close (SIGTERM) of the first running program.

    ``pkill -TERM -x`` matches the process name exactly and asks it to quit, so
    it never force-kills or touches unrelated processes. Returns True if a
    matching process was signalled.
    """
    if not shutil.which("pkill"):
        return False
    for cmd in candidates:
        try:
            result = subprocess.run(
                ["pkill", "-TERM", "-x", cmd],
                stdin=_DEVNULL, stdout=_DEVNULL, stderr=_DEVNULL,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return True
    return False


def _clean(text, pattern):
    """Remove every match of `pattern` and collapse leftover whitespace."""
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.IGNORECASE)
    return re.sub(r"\s+", " ", pattern.sub(" ", text)).strip(" .,!?")


# --------------------------------------------------------------------------- #
# patterns
# --------------------------------------------------------------------------- #

# A search verb means the user wants information (not a fixed local action), so
# the router deliberately does NOT handle it: it returns None and the request
# goes to the agent, which can call the search tools.
SEARCH_VERB = re.compile(r"\b(search|look ?up|find)\b", re.I)

VOL_WORD = re.compile(r"\b(volume|vol|sound|audio)\b", re.I)
VOL_MUTE = re.compile(r"\bmute\b", re.I)
VOL_UP = re.compile(r"\bup\b|\bincrease\b", re.I)
VOL_DOWN = re.compile(r"\bdown\b|\bdecrease\b", re.I)

SYS_STATUS = re.compile(r"\b(cpu|ram|memory|status|disk|system)\b", re.I)

TIME_WORD = re.compile(r"\btime\b", re.I)
DATE_WORD = re.compile(r"\bdate\b|\btoday'?s date\b", re.I)

YT_WORD = re.compile(r"\byoutube\b", re.I)
# Words removed from a YouTube request before it is used as a search query.
YT_STRIP = re.compile(
    r"\b(youtube|play|watch|listen to|listen|open|launch|start|run|show|on)\b",
    re.I,
)
# Only an explicit media verb turns a YouTube mention into a play/search. With no
# verb, a bare "youtube" is NOT treated as a search query.
YT_ACTION = re.compile(r"\b(play|watch|listen to|listen|search|find|look ?up)\b", re.I)

OPEN_VERB = re.compile(r"\b(open|launch|start|run|show)\b", re.I)

# "close/quit/stop/kill X" is its own command (handled before anything else) and
# is never treated as a search query. A *bare* exit word stays a session command.
CLOSE_WORD = re.compile(r"\b(close|quit|stop|kill|exit)\b", re.I)
CLOSE_STRIP = re.compile(
    r"\b(close|quit|stop|kill|exit|the|a|an|app|application|window|tab|browser"
    r"|please|jarvis)\b",
    re.I,
)
BROWSER_WORD = re.compile(r"\b(browser|tab|chrome|chromium|firefox)\b", re.I)

APPS = [
    ("Chrome", re.compile(r"\b(chrome|chromium|google[- ]chrome)\b", re.I),
     ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]),
    ("Firefox", re.compile(r"\bfirefox\b", re.I),
     ["firefox"]),
    ("VS Code", re.compile(r"\b(vs ?code|vscode|code)\b", re.I),
     ["code", "codium", "vscodium", "code-insiders"]),
    ("Terminal", re.compile(r"\b(terminal|term|console|shell)\b", re.I),
     ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
      "alacritty", "kitty", "xterm"]),
    ("Files", re.compile(r"\b(files?|file manager|nautilus)\b", re.I),
     ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm", "xdg-open"]),
    ("Calculator", re.compile(r"\b(calculator|calc)\b", re.I),
     ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "xcalc"]),
    ("VLC", re.compile(r"\bvlc\b", re.I), ["vlc"]),
]

_BARE = {
    "chrome", "firefox", "vscode", "code", "terminal", "term", "files",
    "calculator", "calc", "vlc",
}


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #

def try_local(text):
    """Handle a command locally; return a reply string or None if unmatched.

    Commands containing a search verb are always left to the agent.
    """
    if not text or not text.strip():
        return None
    t = text.strip()

    # Search requests belong to the agent (it can call the search tools).
    if SEARCH_VERB.search(t):
        return None

    # --- volume --------------------------------------------------------- #
    if VOL_WORD.search(t) or VOL_MUTE.search(t):
        sink = "@DEFAULT_SINK@"
        if VOL_MUTE.search(t):
            if _run(["pactl", "set-sink-mute", sink, "toggle"]):
                return "Volume muted."
        elif VOL_UP.search(t):
            if _run(["pactl", "set-sink-volume", sink, "+10%"]):
                return "Volume up."
        elif VOL_DOWN.search(t):
            if _run(["pactl", "set-sink-volume", sink, "-10%"]):
                return "Volume down."
        return None

    # --- system status -------------------------------------------------- #
    if SYS_STATUS.search(t):
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return f"CPU {cpu:.0f}%, RAM {ram:.0f}%, Disk {disk:.0f}%"

    # --- time ----------------------------------------------------------- #
    if TIME_WORD.search(t):
        return f"Current time: {datetime.now().strftime('%H:%M:%S')}"

    # --- date ----------------------------------------------------------- #
    if DATE_WORD.search(t):
        return f"Today is {datetime.now().strftime('%A, %d %B %Y')}"

    # --- close / quit / stop <target> ----------------------------------- #
    # Handled before YouTube/apps so "close youtube" can never become a search
    # query for the word "close".
    if CLOSE_WORD.search(t):
        target = _clean(t, CLOSE_STRIP)
        if target:
            for name, pattern, candidates in APPS:
                if pattern.search(t):
                    if _close_app(candidates):
                        return f"Closing {name}."
                    return f"I don't see {name} running."
            if YT_WORD.search(t) or BROWSER_WORD.search(t):
                return "I can't close browser tabs yet."
        # Unknown target, or a bare "stop"/"quit"/"exit": fall through (the
        # branches below return None) so the agent can decide.

    # --- youtube: open, or play/search only with an explicit media verb --- #
    if YT_WORD.search(t):
        if YT_ACTION.search(t):
            query = _clean(t, YT_STRIP)
            if query:
                _open(f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}")
                return f"Playing {query} on YouTube."
            _open("https://www.youtube.com")
            return "Opening YouTube."
        if OPEN_VERB.search(t):
            _open("https://www.youtube.com")
            return "Opening YouTube."
        # "youtube" with no explicit action -> leave it to the agent.
        return None

    # --- open application ----------------------------------------------- #
    bare = re.sub(r"\s+", "", t.lower()).strip("!?.,")
    wants_open = bool(OPEN_VERB.search(t)) or bare in _BARE
    if wants_open:
        for name, pattern, candidates in APPS:
            if pattern.search(t):
                if _launch(candidates):
                    return f"Opening {name}."
                return f"I couldn't find {name} installed."

    return None
