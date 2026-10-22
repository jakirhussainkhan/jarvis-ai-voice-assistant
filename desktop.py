"""Desktop actions for Jarvis: apps, YouTube, media, volume, brightness, power.

This module is the **OS-agnostic facade**: it holds the fuzzy app matching, the
YouTube/media decision logic and the protected-process rules, and delegates every
platform-specific operation to :mod:`platform_adapters` (``Audio``, ``Window``,
``System``). No Linux command is hard-coded here any more -- the concrete tools
(``arecord``, ``pactl``, ``playerctl``, ``wmctrl``, ``xdotool``,
``brightnessctl``, ...) live only in the platform adapters.

Every action here is explicit and whitelisted. Jarvis never uses ``sudo`` and
never executes arbitrary shell commands from speech -- only the fixed actions
built here and in the adapters. Destructive/power actions are invoked through
``confirm.py`` so they only run after a spoken "yes".
"""

import os
import platform
import re
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import psutil

from platform_adapters import Audio, Window, System
from platform_adapters import common

# Set JARVIS_DEBUG=1 to print desktop-action diagnostics.
_DEBUG = os.getenv("JARVIS_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def _dbg(*parts):
    if _DEBUG:
        print("[desktop]", *parts, flush=True)


# --------------------------------------------------------------------------- #
# generic helpers (thin, patchable wrappers over the shared platform layer)
# --------------------------------------------------------------------------- #

def is_wayland():
    """True when the session is Wayland (X11 window tools do not work there)."""
    return common.is_wayland()


def has(tool):
    """True if an executable is on PATH."""
    return common.has(tool)


def _run(args, timeout=6):
    """Run a command, never raising. Returns ``(returncode, output)``."""
    return common.run(args, timeout=timeout)


def _spawn(args):
    """Start a detached program with its output discarded. True on success."""
    return common.spawn(args)


def _open_url(url):
    """Open a URL in the default browser (patchable wrapper)."""
    return common.open_url(url)


# Protected processes -- re-exported from the shared layer for callers/tests.
PROTECTED_NAMES = common.PROTECTED_NAMES
_is_protected = common.is_protected
_protected_pids = common.protected_pids
_matching_pids = common.matching_pids
_terminate = common.terminate
matching_process_pids = common.matching_process_pids


# --------------------------------------------------------------------------- #
# desktop application index (fuzzy matching over .desktop entries)
# --------------------------------------------------------------------------- #

class App:
    """One launchable desktop application."""

    def __init__(self, desktop_id, name, exec_bin, keywords, path):
        self.desktop_id = desktop_id      # e.g. "org.telegram.desktop"
        self.name = name                  # e.g. "Telegram Desktop"
        self.exec_bin = exec_bin or ""    # e.g. "telegram-desktop"
        self.keywords = keywords          # list[str]
        self.path = path                  # full .desktop path

    def weighted_keys(self):
        """(key, weight) pairs; a name match is trusted more than a keyword."""
        yield self.name, 1.0
        if self.desktop_id:
            yield self.desktop_id, 0.95
            suffix = self.desktop_id.split(".")[-1]
            if len(suffix) >= 4 and suffix.lower() != "desktop":
                yield suffix, 0.92
        if self.exec_bin and self.exec_bin.lower() != "desktop":
            yield self.exec_bin, 0.9
        for keyword in self.keywords:
            if len(keyword) >= 3:
                yield keyword, 0.8

    def __repr__(self):
        return f"App({self.name!r}, id={self.desktop_id!r})"


def _app_dirs():
    home = Path.home()
    dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        home / ".local/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    # Extra data dirs (Nix, custom XDG setups, ...).
    for extra in os.environ.get("XDG_DATA_DIRS", "").split(":"):
        if extra:
            dirs.append(Path(extra) / "applications")
    seen, out = set(), []
    for d in dirs:
        if d.is_dir() and str(d) not in seen:
            seen.add(str(d))
            out.append(d)
    return out


def _windows_app_dirs():
    """Start Menu folders that hold installed-application shortcuts."""
    roots = []
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            roots.append(Path(base) / "Microsoft" / "Windows" / "Start Menu"
                         / "Programs")
    seen, out = set(), []
    for root in roots:
        key = os.path.normcase(str(root))
        if root.is_dir() and key not in seen:
            seen.add(key)
            out.append(root)
    return out


# Shortcuts that are not launchable apps and only add noise to voice matching.
_NOT_AN_APP = re.compile(
    r"\b(uninstall|uninst|readme|release notes|help|manual|documentation|docs?"
    r"|website|homepage|license|changelog)\b", re.I)


def _windows_app_index():
    """Installed apps from the Start Menu's ``.lnk``/``.url`` shortcuts.

    Windows has no ``.desktop`` files, so the Linux scan produced an empty index
    there and every "open chrome" request fell through to "I couldn't find that
    app". The shortcut name is the app name, and launching goes through
    ``os.startfile`` on the shortcut itself, so the target need not be parsed.
    """
    apps, seen = [], set()
    for root in _windows_app_dirs():
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".lnk", ".url") or not path.is_file():
                continue
            name = path.stem.strip()
            key = name.lower()
            if not name or key in seen or _NOT_AN_APP.search(name):
                continue
            seen.add(key)
            apps.append(App(re.sub(r"\s+", "-", key), name, "", [], path))
    return apps


def _parse_desktop(path):
    """Parse the ``[Desktop Entry]`` section of a .desktop file, or None."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_entry = False
    name = generic = exec_line = None
    keywords = []
    no_display = hidden = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "Name" and name is None:
            name = value
        elif key == "GenericName" and generic is None:
            generic = value
        elif key == "Keywords":
            keywords += [k.strip() for k in re.split(r"[;,]", value) if k.strip()]
        elif key == "Exec" and exec_line is None:
            exec_line = value
        elif key == "NoDisplay" and value.lower() == "true":
            no_display = True
        elif key == "Hidden" and value.lower() == "true":
            hidden = True

    if not name or no_display or hidden:
        return None

    exec_bin = ""
    if exec_line:
        first = exec_line.split()[0]
        exec_bin = os.path.basename(first)
    if generic:
        keywords.append(generic)
    desktop_id = path.name[:-len(".desktop")] if path.name.endswith(".desktop") else path.name
    return App(desktop_id, name, exec_bin, keywords, path)


_INDEX = None


def app_index(refresh=False):
    """Cached list of installed :class:`App` entries.

    Linux reads ``.desktop`` files; Windows reads Start Menu shortcuts. macOS
    has neither, so the scan there returns an empty list and the agent falls back
    to launching by name.
    """
    global _INDEX
    if _INDEX is not None and not refresh:
        return _INDEX
    if platform.system() == "Windows":
        _INDEX = _windows_app_index()
        return _INDEX
    apps = []
    for directory in _app_dirs():
        for path in sorted(directory.glob("*.desktop")):
            app = _parse_desktop(path)
            if app is not None:
                apps.append(app)
    _INDEX = apps
    return apps


def _normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _score(query, key):
    if not query or not key:
        return 0.0
    if query == key:
        return 1.0
    ratio = SequenceMatcher(None, query, key).ratio()
    if len(query) >= 3 and query in key:
        ratio = max(ratio, 0.82)
    elif len(key) >= 3 and key in query:
        # "sound settings" should still match the app named "Sound".
        ratio = max(ratio, 0.80)
    return ratio


def match_app(name, threshold=0.55):
    """Best matching app for a spoken name. Returns ``(app_or_None, score)``.

    ``threshold`` is the minimum score to return an app at all. Callers use a
    higher bar for confident action and a mid band to ask "Did you mean ...?".
    Each key is weighted so a real app *name* beats a generic keyword (e.g.
    "settings" prefers the app called "System Settings" over a keyword hit).
    """
    query = _normalize(name)
    if not query:
        return None, 0.0
    best, best_score = None, 0.0
    for app in app_index():
        for key, weight in app.weighted_keys():
            score = _score(query, _normalize(key)) * weight
            if score > best_score:
                best, best_score = app, score
    if best_score < threshold:
        return None, best_score
    return best, best_score


# --------------------------------------------------------------------------- #
# open / close / list
# --------------------------------------------------------------------------- #

STRONG_MATCH = 0.72   # act directly
WEAK_MATCH = 0.55     # ask "Did you mean ...?"

# WM_CLASS fragments that identify a terminal emulator window. A terminal's
# *title* is just the shell prompt ("jakir@coder: ~"), so closing terminals has
# to match the window class rather than the name.
TERMINAL_CLASSES = (
    "gnome-terminal", "xterm", "kitty", "alacritty", "tilix", "terminal",
)


def _is_terminal_app(app):
    """True when a matched app is a terminal emulator."""
    blob = " ".join((
        getattr(app, "name", "") or "",
        getattr(app, "desktop_id", "") or "",
        getattr(app, "exec_bin", "") or "",
    )).lower()
    return any(key in blob for key in TERMINAL_CLASSES)


def open_app(name):
    """Launch an installed app by fuzzy name. Never guesses."""
    app, score = match_app(name)
    if app is None or score < STRONG_MATCH:
        return "I couldn't find that app."
    if System.launch_app(app):
        return f"Opening {app.name}."
    return f"I couldn't launch {app.name}."


def _window_titles():
    return Window.list_window_titles()


def close_app(app):
    """Gracefully close an app, escalating to SIGKILL after ~3 s."""
    if _is_protected(app.exec_bin) or _is_protected(app.name):
        return f"I won't close {app.name} -- it is protected."
    # Terminals are matched by window class: their titles are shell prompts, so
    # the normal title/process matching would report "not running" wrongly.
    if _is_terminal_app(app) and Window.close_by_class(TERMINAL_CLASSES):
        return f"Closed {app.name}."
    if System.close_app(app):
        return f"Closed {app.name}."
    if _is_terminal_app(app) and Window.close_by_class(TERMINAL_CLASSES):
        return f"Closed {app.name}."
    return f"I don't see {app.name} running."


def close_terminal():
    """Close any open terminal emulator by window class, not by title."""
    if Window.close_by_class(TERMINAL_CLASSES):
        return "Closing the terminal."
    return "I don't see any terminal running."


def list_running():
    """A short summary of what is currently open, for the agent."""
    titles = []
    seen = set()
    for title in _window_titles():
        key = title.lower()
        if key and key not in seen:
            seen.add(key)
            titles.append(title)
    if titles:
        shown = ", ".join(titles[:8])
        more = "" if len(titles) <= 8 else f" (+{len(titles) - 8} more)"
        return f"Open windows: {shown}{more}."
    # No window manager: fall back to notable processes.
    names = []
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name")
        if name and name not in names and not _is_protected(name, proc.info.get("pid")):
            names.append(name)
    names = names[:12]
    return "Running: " + ", ".join(names) + "." if names else "I couldn't detect any open windows."


def run_in_terminal(command):
    """Open a visible terminal and run ``command`` there, leaving it open."""
    return System.run_in_terminal(command)


def dictate_in_terminal(text):
    """Open a terminal, type ``text`` into it and press Enter.

    Unlike :func:`run_in_terminal`, nothing is executed on Jarvis's behalf: the
    command is typed (via ``xdotool``) as if the user had typed it, so it is
    visible and auditable. X11 only -- ``xdotool`` cannot drive Wayland.
    """
    text = "" if text is None else str(text).strip()
    if not text:
        return "What should I type in the terminal?"
    if is_wayland():
        return "I can't type into a terminal on Wayland."
    if not has("xdotool"):
        return "xdotool is not installed, so I can't type in the terminal."
    if not System.open_terminal():
        return "I couldn't find a terminal emulator."
    time.sleep(1.5)                     # let the terminal window draw and focus
    rc, out = _run(["xdotool", "type", "--clearmodifiers", text])
    if rc != 0:
        return f"I couldn't type in the terminal: {out}"
    _run(["xdotool", "key", "Return"])
    return f"Typed in the terminal: {text}"


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

_PLAY_VERB = re.compile(r"\b(play|watch|listen to|listen)\b", re.I)

# STT noise that is extremely unlikely to be part of a song/video title.
_MEDIA_NOISE = re.compile(
    r"\b(what do you mean|i mean|i meant|you know|i am|i'm|im|"
    r"please|jarvis|uh+|um+|hmm+)\b",
    re.I,
)

# Filler *phrases* (an article + a media noun, or "<medium> player").
_MEDIA_FILLER_PHRASE = re.compile(
    r"\b(?:just\s+)?(?:the|a|an|some|any|this|that)\s+"
    r"(?:song|songs|music|video|videos|track|tracks|tune|tunes|player|media|clip)s?\b"
    r"|\b(?:music|media|video)\s+player\b",
    re.I,
)

# Words that carry no song information by themselves.
_MEDIA_FILLER_WORD = re.compile(
    r"\b(the|a|an|this|that|some|any|my|"
    r"song|songs|music|video|videos|track|tracks|tune|tunes|player|media|clip|"
    r"on|to|like|now|just|hey|ok|okay|please|jarvis|it|me|i|am|you|youtube)\b",
    re.I,
)


# "search <song> (and|then) play it": the song sits *before* the play verb.
_SEARCH_START = re.compile(
    r"^\s*(?:(?:please|jarvis|hey\s+jarvis|can\s+you|could\s+you)[\s,]+)*"
    r"(?:search|look\s*up|find)\b\s*(?:for\s+)?",
    re.I,
)
_SEARCH_PLAY_SPLIT = re.compile(
    r"\b(?:and|then)\s+(?:play|watch|start)\b", re.I
)


def _media_tail(text):
    """Strip the play verb, YouTube mentions and filler; return the song name."""
    match = _PLAY_VERB.search(text)
    tail = text[match.end():] if match else text
    # Drop a trailing "on YouTube" (and any other "youtube" mention).
    tail = re.sub(r"\bon\s+youtube\b", " ", tail, flags=re.I)
    tail = re.sub(r"\byoutube\b", " ", tail, flags=re.I)
    tail = _MEDIA_NOISE.sub(" ", tail)
    tail = _MEDIA_FILLER_PHRASE.sub(" ", tail)
    tail = re.sub(r"\s+", " ", tail).strip(" .,!?;:-'\"")
    # Nothing but filler -> no song requested.
    if not re.sub(r"[^A-Za-z0-9]+", "", _MEDIA_FILLER_WORD.sub(" ", tail)):
        return ""
    return tail


def extract_media_query(text):
    """Pull just the song/video name out of a spoken play/search command.

    Handles "play <song> on YouTube" and, importantly, "search <song> and play
    it": in the latter the song comes *before* the play verb, so the ordinary
    "everything after play" rule would keep only "now". Drops unambiguous STT
    noise ("what do you mean", "i am", "please", ...) and filler phrases like
    "the song" / "music player". Words that could belong to a title are kept, so
    "Hey Jude", "Like a Prayer" and "The Nights" survive. Returns "" when only
    filler is left, so the caller can ask "What should I play?".
    """
    if not text:
        return ""
    search = _SEARCH_START.match(text)
    if search:
        rest = text[search.end():]
        split = _SEARCH_PLAY_SPLIT.search(rest)
        if split:
            # Try the words before "and play" first, then the words after it
            # ("search and play <song>").
            for candidate in (rest[:split.start()], rest[split.end():]):
                song = _media_tail(candidate)
                if song:
                    return song
            return ""
        return _media_tail(rest)
    return _media_tail(text)


def _yt_url_search(query):
    return f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"


def _window_has_input_focus(wid):
    """True when ``wid`` really owns the input focus."""
    return Window.has_input_focus(wid)


def _youtube_window():
    """Handle of a visible browser window showing YouTube, or ``None``."""
    return Window.locate_window("YouTube")


def _press_in_window(wid, key, restore=True):
    """Focus ``wid``, send ``key``, then restore the previous focus.

    Chromium ignores synthetic key events sent to an unfocused window, so the
    window genuinely has to own input focus. Returns False when the key could
    not be delivered.
    """
    return Window.send_key(wid, key, restore=restore)


def _reuse_youtube_tab(url):
    """Type ``url`` into the address bar of the already-open YouTube tab.

    A browser opens a *new* tab for every ``open_url``, which is exactly what
    the user dislikes. When a visible YouTube window exists we focus it, press
    ctrl+l (address bar), type the url and hit Return, replacing that tab in
    place. This is only ever done with VERIFIED input focus. Returns False for
    anything except a confirmed navigation.
    """
    if not url or not Window.can_send_keys():
        return False
    wid = _youtube_window()
    if not wid or not Window.activate_window(wid):
        return False
    time.sleep(0.3)
    if not _window_has_input_focus(wid):
        Window.force_focus(wid)
        time.sleep(0.3)
        if not _window_has_input_focus(wid):
            _dbg(f"window {wid} does not have input focus; refusing to type")
            return False
    before = Window.window_name(wid)
    Window.send_keys("ctrl+l")
    time.sleep(0.2)
    Window.type_string(url)
    time.sleep(0.15)
    ok = Window.send_keys("Return")
    time.sleep(2.5)
    after = Window.window_name(wid)
    # The page title must change: anything else means the keys never landed.
    if not ok or after == before:
        _dbg("navigation did not reach the browser; keeping the current tab")
        return False
    return True


def open_youtube():
    """Open YouTube -- or bring the existing YouTube tab to the front."""
    wid = _youtube_window()
    if wid:
        if Window.activate_window(wid) and _window_has_input_focus(wid):
            return "YouTube is already open -- I brought it up."
        # A YouTube tab exists but the window manager refused to raise it. A
        # second tab is never opened just because activation failed.
        return "YouTube is already open, but I couldn't bring it to the front."
    if _open_url("https://www.youtube.com"):
        return "Opening YouTube."
    return "I couldn't open the browser."


def _navigate_youtube(url):
    """Send the browser to ``url``, reusing the open YouTube tab when there is one.

    Returns one of:

    * ``"reused"``  -- navigated the already-open YouTube tab in place.
    * ``"opened"``  -- no YouTube tab was open, so a new one was opened.
    * ``"blocked"`` -- YouTube is open but the in-place navigation failed; a
      second tab is deliberately NOT opened.
    * ``"failed"``  -- no YouTube tab was open and the browser could not open.

    This is the single choke point for YouTube navigation: when a YouTube window
    already exists, ``webbrowser``/``open_url`` is never used, so Jarvis can
    never spawn a duplicate tab.
    """
    if _youtube_window():
        return "reused" if _reuse_youtube_tab(url) else "blocked"
    return "opened" if _open_url(url) else "failed"


def search_youtube(query):
    if not query:
        return open_youtube()
    outcome = _navigate_youtube(_yt_url_search(query))
    if outcome == "reused":
        return f"Searching YouTube for {query}."
    if outcome == "opened":
        return f"Here are the YouTube results for {query}."
    if outcome == "blocked":
        return ("YouTube is already open, but I couldn't navigate the "
                "existing tab.")
    return "I couldn't open the browser."


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def _finish_youtube_play(query, outcome):
    """Wait for the page, make it play, then report the *verified* state.

    The spoken reply must never claim "Playing" before the video really is: the
    page is given two seconds to load and ``ensure_browser_playing`` then checks
    (and if needed nudges) the player. A blocked autoplay is reported honestly so
    the user can press play instead of wondering why nothing happened.
    """
    time.sleep(2.0)                      # let the browser load the new video
    if ensure_browser_playing():
        if outcome == "reused":
            return f"Playing {query} in your open YouTube tab."
        return f"Playing {query} on YouTube."
    if outcome == "reused":
        return (f"I navigated your open YouTube tab to {query}, but it is still "
                "paused -- press play or allow autoplay.")
    return (f"I opened {query} on YouTube, but it is still paused -- "
            "press play or allow autoplay.")


def play_youtube(query):
    """Play the top yt-dlp result for a song/video name; else open the results.

    The argument may be a raw spoken sentence; only the song/video name is used.

    Uses ``--flat-playlist --print id`` so yt-dlp only resolves the search hit's
    id -- it never tries to pick a video format (which fails on YouTube with
    older yt-dlp builds and would silently downgrade us to a results page).

    When a YouTube tab is already open the resolved watch URL (or the results
    URL) is typed into that tab's address bar with ``xdotool`` instead of opening
    a new one; see :func:`_navigate_youtube`.
    """
    query = extract_media_query(query)
    if not query:
        return "What should I play?"
    # The mic stays live and media is never paused: Jarvis keeps listening while
    # the video plays so "pause music" / "next song" work, and the RMS gate keeps
    # the background audio from registering as speech.
    if has("yt-dlp"):
        rc, out = _run(
            ["yt-dlp", f"ytsearch1:{query}", "--flat-playlist",
             "--print", "id", "--no-warnings"],
            timeout=25,
        )
        vid = ""
        for line in out.splitlines():
            line = line.strip()
            if _YT_ID.match(line):
                vid = line
                break
        if rc == 0 and vid:
            watch_url = f"https://www.youtube.com/watch?v={vid}"
            outcome = _navigate_youtube(watch_url)
            if outcome in ("reused", "opened"):
                # Autoplay is normally blocked without a user gesture, so verify
                # (and nudge) playback before promising that it is playing.
                return _finish_youtube_play(query, outcome)
            if outcome == "blocked":
                return ("YouTube is already open, but I couldn't navigate the "
                        "existing tab.")
        elif rc != 0:
            # Surface yt-dlp's own error instead of failing silently.
            _dbg(f"yt-dlp failed (rc={rc}): {out}")
    # yt-dlp missing or failed: navigate to the results page and say so.
    reason = "yt-dlp is not installed" if not has("yt-dlp") else "I couldn't find a video"
    outcome = _navigate_youtube(_yt_url_search(query))
    if outcome == "reused":
        return f"{reason}, so I searched your open YouTube tab for {query}."
    if outcome == "opened":
        return f"{reason}, so I opened the YouTube results for {query}."
    if outcome == "blocked":
        return ("YouTube is already open, but I couldn't navigate the "
                "existing tab.")
    return f"{reason}, and I couldn't open the browser."


def close_youtube():
    """Close the YouTube tab, wherever the browser currently is.

    The visible YouTube window is targeted directly: it is activated and
    ctrl+w closes that tab, with focus restored to whatever was there before.
    Only when no YouTube tab is visible do we fall back to the focused window --
    never guessing outside a browser.
    """
    if not Window.can_send_keys():
        return "Which window?"
    wid = _youtube_window()
    if wid and _press_in_window(wid, "ctrl+w"):
        return "Closing the YouTube tab."
    # Fallback for titles without the " - YouTube" suffix: only close the
    # focused browser tab when a browser media session is live. A stopped
    # browser is never touched.
    if _browser_media_player():
        active = Window.get_active_window_title()
        if active and re.search(r"(youtube|chrome|chromium|firefox|brave|edge)",
                                active, re.I):
            if Window.send_keys("ctrl+w"):
                return "Closing the YouTube tab."
    return "There is no YouTube tab open."


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #

# A browser's MPRIS session is deliberately limited: Chromium/Brave report
# CanGoNext/CanGoPrevious = false, so the player refuses those outright. When we
# can see the YouTube window we can drive the page with its own shortcuts.
_BROWSER_MEDIA = re.compile(r"brave|chrome|chromium|firefox|edge|vivaldi|opera", re.I)


def _browser_media_player():
    """True when a browser is publishing a media session ("did it start?")."""
    return bool(_BROWSER_MEDIA.search(System.media_players()))


def _playerctl_status(timeout=3):
    """playerctl's status in lower case; '' when there is no player.

    Returns ``None`` when playerctl is missing or cannot be run, so callers can
    tell "no media session" (autoplay blocked) apart from "cannot verify".
    """
    if not has("playerctl"):
        return None
    rc, out = _run(["playerctl", "status"], timeout=timeout)
    if rc != 0:
        return ""
    return (out or "").strip().lower()


def ensure_browser_playing(verify_seconds=1.0):
    """Make sure the YouTube video is actually playing; return True when it is.

    Browsers refuse unmuted autoplay until the site has seen a user gesture, and a
    spoken command is not one -- so a video we just navigated to often sits paused
    and may publish no MPRIS session at all. ``playerctl status`` is checked
    first: "Playing" means leave it alone (a key press would pause it). "Paused",
    no session, or a playerctl failure all mean playback is not running, so the
    YouTube window is focused and Space is pressed, then the status is verified
    after ``verify_seconds``. When playerctl is unavailable the delivered key is
    trusted (there is nothing to verify against). Best effort, X11 only, never
    raises.
    """
    status = _playerctl_status()
    if status and "playing" in status:
        return True
    if not Window.can_send_keys():
        return False
    wid = _youtube_window()
    if not wid:
        return False
    if not _press_in_window(wid, "space", restore=False):
        return False
    time.sleep(max(0.5, float(verify_seconds)))
    status = _playerctl_status()
    if status is None:
        return True                  # no playerctl: trust the key we delivered
    return "playing" in status


def _youtube_page_fallback(key):
    """Drive a media key in the browser window (YouTube first, then any browser).

    Used when ``playerctl`` cannot handle the action (Chromium/Brave report
    ``CanGoNext = false`` for skip, and pause may have no MPRIS session at all).
    """
    return _press_in_window(_browser_window(), key)


# A window whose title identifies a browser (used to route media keys when the
# MPRIS session is missing). YouTube-specific windows are preferred.
_BROWSER_WINDOW = re.compile(
    r"(youtube|chrome|chromium|firefox|brave|edge|vivaldi|opera)", re.I
)


def _browser_window():
    """Handle of a visible browser window, or ``None``.

    The YouTube window is preferred; otherwise the first visible window whose
    title looks like a browser is used.
    """
    wid = _youtube_window()
    if wid:
        return wid
    if not Window.can_send_keys():
        return None
    for title in Window.list_window_titles():
        if _BROWSER_WINDOW.search(title or ""):
            wid = Window.locate_window(title)
            if wid:
                return wid
    return None


def media_control(action):
    """Pause/resume/next/previous/stop the active player."""
    return System.media_control(action, page_fallback=_youtube_page_fallback)


def media_seek(seconds):
    """Seek the active player forward/back by ``seconds`` (can be negative)."""
    return System.media_seek(seconds)


def now_playing():
    """Describe the current track, or report that nothing is playing."""
    return System.now_playing()


def pause_media_if_playing():
    """Explicitly pause active media, returning an opaque resume token.

    This is deliberately *not* called when Jarvis listens: background media
    keeps playing so the user can ask for "pause music" or "next song". It
    remains available as a manual media-control primitive. Returns ``None``
    when nothing was playing; never raises.
    """
    return System.pause_media_if_playing()


def resume_media(token):
    """Best-effort resume after :func:`pause_media_if_playing`. Never raises."""
    return System.resume_media(token)


# --------------------------------------------------------------------------- #
# volume / brightness
# --------------------------------------------------------------------------- #

def volume_control(action, value=None):
    """Volume up/down/set/mute/unmute, clamped to 0-100."""
    return Audio.volume_control(action, value)


def brightness_control(action, value=None):
    """Brightness up/down/set, clamped to 1-100."""
    return System.brightness_control(action, value)


# --------------------------------------------------------------------------- #
# screenshot / lock
# --------------------------------------------------------------------------- #

def capture_screenshot(path):
    """Capture the whole screen to ``path``. Returns the Path on success, else None.

    Used by :func:`screenshot` and by :mod:`vision` to feed the screen to the
    vision model. The platform adapter prefers the *silent* tools and never
    raises.
    """
    return System.screenshot(Path(path))


def screenshot():
    """Save a screenshot to ~/Pictures and return the file name."""
    folder = Path.home() / "Pictures"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"jarvis-screenshot-{stamp}.png"
    if capture_screenshot(path) is not None:
        return f"Screenshot saved as {path.name}."
    return "I couldn't take a screenshot."


def lock_screen():
    """Lock the session using whatever locker the desktop provides."""
    return System.lock_screen()


# --------------------------------------------------------------------------- #
# screen / window interaction (whitelisted: move/click/type/key/scroll)
# --------------------------------------------------------------------------- #

def screen_size():
    """Return ``(width, height)`` of the display, or ``None`` if unknown."""
    return Window.screen_size()


def move_mouse(x, y):
    """Move the pointer to absolute ``(x, y)`` pixels."""
    return Window.move_mouse(x, y)


def click_at(x, y, button=1, double=False):
    """Click at absolute ``(x, y)``. ``button`` 1=left, 2=middle, 3=right."""
    return Window.click(x, y, button=button, double=double)


def type_text(text):
    """Type ``text`` into whatever window currently has focus."""
    return Window.type_text(text)


def press_keys(keys):
    """Press a keyboard shortcut or key, e.g. ``ctrl+s``, ``alt+tab``, ``super``."""
    return Window.press_keys(keys)


def scroll(direction, amount=3):
    """Scroll the focused window: direction is 'up' or 'down'."""
    return Window.scroll(direction, amount)


def focus_window(name):
    """Bring the window whose title matches ``name`` to the front."""
    return Window.activate_by_title(name)


def minimize_window(name):
    """Minimise windows whose title matches ``name``."""
    return Window.minimize_by_title(name)


def maximize_window(name):
    """Maximise windows whose title matches ``name``."""
    return Window.maximize_by_title(name)


def list_windows():
    """Enumerate the open top-level windows (one per line) for the agent."""
    titles = _window_titles()
    if not titles:
        return "I couldn't list any windows (no window manager on Wayland?)."
    lines = [f"{i + 1}. {t}" for i, t in enumerate(titles[:20])]
    more = "" if len(titles) <= 20 else f"\n(+{len(titles) - 20} more)"
    return "\n".join(lines) + more


# --------------------------------------------------------------------------- #
# open files / folders / URLs, notifications, clipboard
# --------------------------------------------------------------------------- #

# A bare domain ("example.com", "docs.python.org/index") -> open in the browser.
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|io|dev|app|ai|co|me|info|xyz|"
    r"tv|site|online|store|blog|tech|news|live|cloud|uk|us|ca|de|fr|in|jp|au|"
    r"eu)(?:/\S*)?$",
    re.I,
)


def open_path(target):
    """Open a file, folder or URL with the desktop's default application."""
    target = (target or "").strip()
    if not target:
        return "What should I open?"
    if re.match(r"^(https?://|www\.)", target, re.I):
        url = target if target.lower().startswith("http") else "https://" + target
        return f"Opening {url}." if _open_url(url) else "I couldn't open the browser."
    path = os.path.expanduser(target)
    if os.path.exists(path):
        return System.open_path(path)
    # A bare domain opens in the browser; anything else goes to the desktop.
    if _DOMAIN_RE.match(target):
        url = "https://" + target
        return f"Opening {url}." if _open_url(url) else "I couldn't open the browser."
    return System.open_path(path)


# Common folders a spoken name can mean.
_FOLDER_NAMES = {
    "home": "~",
    "downloads": "~/Downloads",
    "download": "~/Downloads",
    "documents": "~/Documents",
    "document": "~/Documents",
    "docs": "~/Documents",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "images": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "movies": "~/Videos",
    "desktop": "~/Desktop",
    "templates": "~/Templates",
    "public": "~/Public",
}


def resolve_folder(name):
    """Return an existing path for a spoken folder name, or ``None``."""
    if not name:
        return None
    key = _normalize(name)
    key = re.sub(r"\b(folder|directory|dir|my|the|our|a|an)\b", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    for candidate in (key, key.replace(" ", "")):
        raw = _FOLDER_NAMES.get(candidate)
        if raw:
            path = os.path.expanduser(raw)
            if os.path.isdir(path):
                return path
    # A bare name that is an existing directory directly under the home folder.
    path = os.path.expanduser(os.path.join("~", name.strip()))
    return path if os.path.isdir(path) else None


def notify(message, title="JARVIS"):
    """Show a desktop notification."""
    return System.notify(message, title)


def clipboard(action):
    """Copy / cut / paste in the focused window, or read the clipboard text."""
    action = (action or "").strip().lower()
    shortcut = {"copy": "ctrl+c", "cut": "ctrl+x", "paste": "ctrl+v",
                "select all": "ctrl+a"}
    if action in shortcut:
        return press_keys(shortcut[action])
    if action in ("read", "get", "show", "what", "contents", "text"):
        text = System.read_clipboard()
        if text is None:
            text = _qt_clipboard_read()
            if text is None:
                return ("I can't read the clipboard on this system, but copy, "
                        "cut and paste still work.")
        return (text.strip()[:600] or "The clipboard is empty.")
    return "I can copy, cut, paste or read the clipboard."


# Set when we create a QApplication just to read the clipboard. Kept alive for
# the process lifetime so Qt does not tear the clipboard selection down.
_QT_APP = None


def _qt_clipboard_read():
    """Read the clipboard with Qt (PyQt5) when no native tool is available.

    Returns the clipboard text, or ``None`` if Qt is unavailable. Reuses the
    running GUI's QApplication when there is one.
    """
    global _QT_APP
    try:
        from PyQt5.QtWidgets import QApplication
    except Exception:
        return None
    app = QApplication.instance() or _QT_APP
    if app is None:
        try:
            app = QApplication([])
            _QT_APP = app
        except Exception:
            return None
    try:
        return app.clipboard().text()
    except Exception:
        return None


def find_files(query, limit=15):
    """Find files under the home directory whose name matches ``query``."""
    return System.find_files(query, limit)


# --------------------------------------------------------------------------- #
# power (only ever called after a spoken confirmation)
# --------------------------------------------------------------------------- #

def power(kind):
    """Run a confirmed power action: shutdown / restart / logout / suspend."""
    return System.power(kind)
