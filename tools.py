"""Tool definitions Jarvis can invoke on the local Linux system.

Each tool is a plain Python function with type hints and a docstring. The agent
reads that signature to build the OpenAI-compatible tool declaration, so there
is no hand-written TOOL_SCHEMA or DISPATCH table to maintain.

All system actions go through :mod:`desktop`, which only ever runs whitelisted
commands (never ``sudo``, never arbitrary shell). Destructive power actions are
not exposed here at all -- they are handled by the router through
:mod:`confirm`, so a stray utterance can never shut the machine down.

The CodeAct tool is the one exception: it runs a user/agent-authored Python
script in a subprocess, guarded by an AST allow/deny pass and a hard timeout.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

import psutil

import confirm
import desktop
from platform_adapters.common import hidden_process_kwargs
import memory as memory_store
import net
import shell
import timers
import vision

_NOTES_PATH = os.path.expanduser("~/jarvis_notes.md")
_READ_LIMIT = 1500

# The conversation Memory for the current agent turn. run_agent() sets this so
# fact tools write to exactly the same store that supplies the prompt. Direct
# tool calls fall back to the shared default store.
_ACTIVE_MEMORY = ContextVar("jarvis_active_memory", default=None)


def set_active_memory(memory):
    """Bind ``memory`` to the current turn and return a reset token."""
    return _ACTIVE_MEMORY.set(memory)


def reset_active_memory(token):
    """Restore the previous turn's memory binding."""
    _ACTIVE_MEMORY.reset(token)


def _fact_memory():
    """Return the Memory store used by the long-term fact tools."""
    active = _ACTIVE_MEMORY.get()
    return active if active is not None else memory_store.default_memory()


# Directories fn_read_file may read from. Anything outside this allowlist is
# refused, so a misheard or hostile path cannot be used to walk the filesystem.
# The roots are resolved on every call (not once at import) so tests and a
# relocated HOME behave correctly.
_READ_DENIED = "Access denied. You can only read files in allowed directories."


def _read_roots():
    """Resolved directories fn_read_file is allowed to read from."""
    home = Path.home()
    candidates = [
        Path(__file__).resolve().parent,   # the project itself
        home / "Documents",
        home / "Downloads",
        Path("/tmp"),
        Path(tempfile.gettempdir()),       # cross-platform temp directory
    ]
    roots = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _within_allowlist(path: Path) -> bool:
    """True when ``path`` lives inside one of the allowed read directories."""
    return any(path == root or root in path.parents for root in _read_roots())


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


# --------------------------------------------------------------------------- #
# live internet (see net.py) -- lets the model reason over fresh information
# --------------------------------------------------------------------------- #

def fn_web_lookup(query: str) -> str:
    """Search the live web and return the top results (titles, URLs, snippets).

    Use this for anything current or factual you are not sure about -- news,
    scores, prices, releases, "who is", "what happened", "latest version". Read
    the snippets and answer directly, or call fn_fetch_page on a result for detail.
    """
    return net.format_results(query)


def fn_fetch_page(url: str) -> str:
    """Fetch a web page and return its readable text.

    Use after fn_web_lookup to read an article, documentation page or changelog in
    full before answering.
    """
    return net.fetch_page(url)


def fn_wikipedia(topic: str) -> str:
    """Return the Wikipedia summary of a person, place or thing."""
    return net.wikipedia(topic)


def fn_weather(city: str = "") -> str:
    """Report the current weather for `city` (or the local area if left empty)."""
    return net.weather(city)


def fn_save_note(text: str) -> str:
    """Append a timestamped note to ~/jarvis_notes.md."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_NOTES_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"- [{stamp}] {text}\n")
    except OSError as exc:
        return f"Could not save note: {exc}"
    return f"Note saved to {_NOTES_PATH}."


def fn_remember_fact(fact: str) -> str:
    """Save a personal fact, preference, or name for future sessions.

    Use this whenever the user says something durable about themselves, such as
    "my name is Jakir", "I prefer tea", or "remember my birthday is in May".
    Do not use it for temporary details that only matter in this conversation.
    """
    fact = " ".join(str(fact or "").split())
    if not fact:
        return "What fact should I remember?"
    try:
        saved = _fact_memory().add_fact(fact)
    except Exception as exc:
        return f"I couldn't save that fact: {exc}"
    if not saved:
        return "I couldn't save that fact."
    return f"I'll remember that: {fact}."


def fn_recall_facts() -> str:
    """Return the personal facts saved about the user."""
    try:
        facts = _fact_memory().get_facts(limit=None)
    except Exception as exc:
        return f"I couldn't read my saved facts: {exc}"
    if not facts:
        return "I don't have any saved facts about you yet."
    return "Here is what I remember:\n" + "\n".join(
        f"- {fact}" for fact in facts
    )


def fn_forget_fact(keyword: str) -> str:
    """Delete saved personal facts that match ``keyword``.

    Use this when the user asks you to forget something ("forget my name",
    "forget my birthday") or when they correct a fact you already saved -- for
    example, delete the old name before saving a new one. Matching is
    case-insensitive and substring-based, so "name" removes "my name is Jakir".
    """
    keyword = " ".join(str(keyword or "").split())
    if not keyword:
        return "What should I forget?"
    try:
        removed = _fact_memory().delete_fact(keyword)
    except Exception as exc:
        return f"I couldn't forget that fact: {exc}"
    if not removed:
        return f"I don't have any saved facts matching {keyword}."
    return f"Forgot facts matching {keyword}"


def fn_read_file(path: str) -> str:
    """Return the first 1500 characters of a text file.

    Reading is sandboxed: only files inside the project, ~/Documents,
    ~/Downloads or the temp directory are allowed, so a stray path can never be
    used to read the wider filesystem.
    """
    try:
        full = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return f"Could not read {path}."

    if not _within_allowlist(full):
        return _READ_DENIED

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


# --------------------------------------------------------------------------- #
# terminal (guarded -- see shell.py)
# --------------------------------------------------------------------------- #

def fn_run_command(command: str, cwd: str = "") -> str:
    """Run a terminal command and return its output.

    Use this to inspect and use the system like an expert: ls, cat, grep, find,
    df, ps, uname, git status, python --version, ffmpeg, etc. Never runs sudo or
    root commands, and refuses catastrophic ones. Commands that change files or
    packages (rm, mv, chmod, apt, systemctl, writing with '>') are read back to
    the user and only run after they say yes. `cwd` is an optional directory.
    """
    return shell.run(command, cwd or None)


def fn_run_in_terminal(command: str) -> str:
    """Open the visible terminal and run `command` there, instead of hidden.

    Use this when the user wants to *watch* the command run (builds, installers,
    long output). The same safety rules as fn_run_command apply.
    """
    command = (command or "").strip()
    if not command:
        return "Which command should I run?"
    refusal = shell.explain_refusal(command)
    if refusal:
        return f"I won't run that: {refusal}"
    if shell._is_risky(command):
        return confirm.request(lambda c=command: desktop.run_in_terminal(c),
                               f"Run this in a terminal? {command}")
    return desktop.run_in_terminal(command)


# --------------------------------------------------------------------------- #
# CodeAct: run a self-contained Python script
# --------------------------------------------------------------------------- #

_EXEC_TIMEOUT_SECONDS = 15
_EXEC_OUTPUT_LIMIT = 4000

# This is a guardrail, not a security sandbox. It blocks the obvious escape
# hatches (shell/process/file-tree modules and introspection) before a script is
# allowed to run, which is enough to keep a mis-generated script from casually
# deleting the machine while remaining useful for normal Python.
_PY_BLOCKED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "socket", "ctypes", "multiprocessing",
    "pty", "resource", "signal", "importlib", "builtins", "pickle", "marshal",
    "shelve", "urllib", "http", "ftplib", "telnetlib",
})
_PY_BLOCKED_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "exit", "quit", "input",
    "breakpoint", "SystemExit", "globals", "locals", "vars", "getattr",
    "setattr", "delattr",
})
_PY_BLOCKED_ATTRS = frozenset({
    "system", "popen", "rmtree", "rmdir", "unlink", "kill", "fork",
    "execv", "execve", "spawn", "exit",
})
# Common harmless introspection dunders, so normal script idioms still work.
_PY_ALLOWED_DUNDERS = frozenset({"__name__", "__file__", "__doc__"})


def _dotted_name(node):
    """Return ``a.b.c`` for an AST Name/Attribute chain, else an empty string."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _python_violation(script):
    """Return a human-readable reason a script is unsafe, or None when allowed."""
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return f"syntax error at line {exc.lineno}: {exc.msg}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _PY_BLOCKED_MODULES:
                    return f"import of '{alias.name}' is not allowed"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _PY_BLOCKED_MODULES:
                return f"import from '{node.module}' is not allowed"
            for alias in node.names:
                if alias.name in _PY_BLOCKED_CALLS:
                    return f"import of '{alias.name}' is not allowed"
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name in _PY_BLOCKED_CALLS:
                return f"call to '{name}' is not allowed"
            if any(part in _PY_BLOCKED_MODULES for part in name.split(".")):
                return f"call to '{name}' is not allowed"
        elif isinstance(node, ast.Attribute):
            if node.attr in _PY_BLOCKED_ATTRS:
                return f"access to '{node.attr}' is not allowed"
            if (node.attr.startswith("__") and node.attr.endswith("__")
                    and node.attr not in _PY_ALLOWED_DUNDERS):
                return f"access to '{node.attr}' is not allowed"
            name = _dotted_name(node)
            if any(part in _PY_BLOCKED_MODULES for part in name.split(".")):
                return f"access to '{name}' is not allowed"
        elif isinstance(node, ast.Name):
            if node.id in _PY_BLOCKED_CALLS:
                return f"use of '{node.id}' is not allowed"
            if (node.id.startswith("__") and node.id.endswith("__")
                    and node.id not in _PY_ALLOWED_DUNDERS):
                return f"access to '{node.id}' is not allowed"
    return None


def fn_execute_python(script: str) -> str:
    """Run a short, self-contained Python script and return its output.

    Use this for genuine multi-step work -- creating several files, complex
    calculations, transforming data or batch operations -- instead of chaining
    many individual tools. The script runs with the current Python interpreter,
    the normal working directory and a 15 second timeout.

    Only safe standard-library code is allowed. Imports of os/subprocess/shutil/
    sys and dangerous operations such as eval, exec, sys.exit or shell calls are
    refused before anything executes.
    """
    script = "" if script is None else str(script)
    if not script.strip():
        return "The Python script is empty."

    violation = _python_violation(script)
    if violation:
        return f"Refused to run the script: {violation}."

    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="jarvis-codeact-", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_EXEC_TIMEOUT_SECONDS,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            **hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return f"Python script timed out after {_EXEC_TIMEOUT_SECONDS} seconds."
    except OSError as exc:
        return f"I couldn't run the Python script: {exc}"
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode:
        output = f"Exit code {proc.returncode}.\n{output}".strip()
    if not output:
        return "Python script finished with no output."
    if len(output) > _EXEC_OUTPUT_LIMIT:
        output = output[:_EXEC_OUTPUT_LIMIT] + "\n...[truncated]"
    return output


# --------------------------------------------------------------------------- #
# screen vision / automation (see vision.py, desktop.py)
# --------------------------------------------------------------------------- #

def fn_see_screen(question: str = "") -> str:
    """Look at the screen and answer `question` about it.

    Leave `question` empty to get a short description of what is on screen. Use
    this for "what's on my screen", "read this", "summarise the page".
    """
    return vision.look(question or None)


_SCREEN_RISKY = re.compile(
    r"\b(delete|remove|uninstall|format|erase|wipe|reset|terminate|shutdown|"
    r"restart|suspend|power ?off|buy|purchase|pay|payment|checkout|order|"
    r"transfer|withdraw|bank|password|send|submit|post|publish|tweet|email|"
    r"subscribe|accept|decline)\b",
    re.I,
)


def screen_do(goal):
    """Look at the screen and act on it, confirming destructive goals first.

    The shared implementation behind :func:`fn_screen_do` and the router's
    on-screen action branch, so "play the video on my screen" behaves the same
    whether the fast-path router or the agent handles it.
    """
    goal = (goal or "").strip()
    if not goal:
        return "What should I do on the screen?"
    if _SCREEN_RISKY.search(goal):
        return confirm.request(lambda g=goal: vision.act(g),
                               f"Control the screen to {goal}?")
    return vision.act(goal)


def fn_screen_do(goal: str) -> str:
    """Achieve a goal by looking at the screen and clicking/typing on it.

    Use for direct UI actions like "click the login button", "open the first
    result", "close that popup", "play the video that is on screen" or "fill in
    the search box and search". Goals that sound destructive or financial are
    confirmed with the user first.
    """
    return screen_do(goal)


def fn_click(x: int, y: int, button: int = 1, double: bool = False) -> str:
    """Click at absolute screen coordinates. button 1=left, 3=right."""
    return desktop.click_at(x, y, button=button, double=double)


def fn_move_mouse(x: int, y: int) -> str:
    """Move the mouse pointer to absolute screen coordinates."""
    return desktop.move_mouse(x, y)


def fn_type_text(text: str) -> str:
    """Type `text` into the currently focused window."""
    return desktop.type_text(text)


def fn_press_keys(keys: str) -> str:
    """Press a keyboard shortcut, e.g. 'ctrl+s', 'alt+tab', 'super', 'enter'."""
    return desktop.press_keys(keys)


def fn_scroll(direction: str = "down", amount: int = 3) -> str:
    """Scroll the focused window: direction 'up' or 'down'."""
    return desktop.scroll(direction, amount)


# --------------------------------------------------------------------------- #
# windows / paths / clipboard / notifications
# --------------------------------------------------------------------------- #

def fn_list_windows() -> str:
    """List the titles of the currently open windows."""
    return desktop.list_windows()


def fn_focus_window(name: str) -> str:
    """Bring the window whose title contains `name` to the front."""
    return desktop.focus_window(name)


def fn_minimize_window(name: str) -> str:
    """Minimise the window whose title contains `name`."""
    return desktop.minimize_window(name)


def fn_maximize_window(name: str) -> str:
    """Maximise the window whose title contains `name`."""
    return desktop.maximize_window(name)


def fn_open_path(path: str) -> str:
    """Open a file, folder or URL with the default application.

    Use for 'open my downloads folder', 'open this document', a website, etc.
    """
    return desktop.open_path(path)


def fn_notify(message: str, title: str = "Jarvis") -> str:
    """Show a desktop notification with `message`."""
    return desktop.notify(message, title)


def fn_clipboard(action: str) -> str:
    """Clipboard: action 'copy', 'cut', 'paste' or 'read'."""
    return desktop.clipboard(action)


def fn_find_files(query: str) -> str:
    """Find files under the home folder whose name matches `query`."""
    return desktop.find_files(query)


# --------------------------------------------------------------------------- #
# media (extras) / system / timers
# --------------------------------------------------------------------------- #

def fn_media_seek(seconds: int) -> str:
    """Seek the current track forward (positive) or back (negative) by seconds."""
    return desktop.media_seek(seconds)


def fn_now_playing() -> str:
    """Report the artist and title of the media that is currently playing."""
    return desktop.now_playing()


def fn_system_info() -> str:
    """Report a fuller system status: CPU, RAM, disk, uptime, battery, user."""
    cpu = psutil.cpu_percent(interval=0.4)
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
    hours, minutes = divmod(int(uptime.total_seconds()) // 60, 60)
    parts = [
        f"CPU {cpu:.0f}%",
        f"RAM {vm.percent:.0f}% ({vm.used // 2**20} of {vm.total // 2**20} MB)",
        f"Disk {du.percent:.0f}%",
        f"up {hours}h {minutes}m",
    ]
    battery = psutil.sensors_battery()
    if battery is not None:
        state = "plugged in" if battery.power_plugged else "on battery"
        parts.append(f"battery {battery.percent:.0f}% {state}")
    parts.append(f"user {os.environ.get('USER', 'you')}")
    return ", ".join(parts)


def fn_set_timer(seconds: int, label: str = "Timer") -> str:
    """Set a countdown timer that speaks when it finishes."""
    return timers.set_timer(seconds, label)


def fn_set_reminder(text: str, minutes: int = 5) -> str:
    """Remind the user about `text` in `minutes` minutes."""
    return timers.set_reminder(text, minutes)


def fn_kill_process(name: str) -> str:
    """Stop a running process by name. Asks for confirmation first."""
    name = (name or "").strip()
    if not name:
        return "Which process should I stop?"
    pids = desktop.matching_process_pids(name)
    if not pids:
        return f"I don't see a process called {name}."
    count = len(pids)

    def stop():
        desktop._terminate(pids)
        return f"Stopped {count} process(es) matching {name}."

    return confirm.request(stop, f"Stop {count} process(es) matching {name}?")
