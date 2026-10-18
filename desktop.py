"""Desktop actions for Jarvis: apps, YouTube, media, volume, brightness, power.

Every action here is explicit and whitelisted. Jarvis never uses ``sudo`` and
never executes arbitrary shell commands from speech -- only the fixed commands
built in this module. Destructive/power actions are invoked through
``confirm.py`` so they only run after a spoken "yes".

Session is X11/Cinnamon here. On Wayland ``wmctrl``/``xdotool`` do not work, so
window operations are skipped and we fall back to process control / ``playerctl``;
:func:`close_youtube` and :func:`close_app` degrade to asking which window.
"""

import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import psutil

_DEVNULL = subprocess.DEVNULL

# Set JARVIS_DEBUG=1 to print desktop-action diagnostics.
_DEBUG = os.getenv("JARVIS_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def _dbg(*parts):
    if _DEBUG:
        print("[desktop]", *parts, flush=True)

# --------------------------------------------------------------------------- #
# environment / process helpers
# --------------------------------------------------------------------------- #

def is_wayland():
    """True when the session is Wayland (window tools like wmctrl don't work)."""
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def has(tool):
    """True if an executable is on PATH."""
    return shutil.which(tool) is not None


def _run(args, timeout=6):
    """Run a command, never raising. Returns ``(returncode, output)``.

    ``returncode`` is 127 when the program is missing.
    """
    if not args:
        return 127, ""
    if shutil.which(args[0]) is None:
        return 127, f"{args[0]} is not installed"
    try:
        proc = subprocess.run(
            args,
            stdin=_DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _spawn(args):
    """Start a detached program with its output discarded. True on success."""
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


def _open_url(url):
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# protected processes -- never closed or touched
# --------------------------------------------------------------------------- #

# Display server, session manager, systemd and the desktop shell.
PROTECTED_NAMES = {
    "systemd", "systemd-journal", "systemd-journald", "systemd-logind",
    "systemd-udevd", "init", "dbus-daemon", "dbus-broker",
    "xorg", "xwayland", "x", "xinit",
    "cinnamon", "cinnamon-session", "cinnamon-screensaver", "cinnamon-launcher",
    "gnome-shell", "gnome-session", "gnome-session-binary",
    "plasmashell", "plasma_session", "ksmserver", "kwin_x11", "kwin_wayland",
    "mutter", "xfwm4", "xfce4-session", "marco", "metacity",
    "lightdm", "gdm", "gdm3", "sddm", "lxdm",
    "pipewire", "pipewire-pulse", "wireplumber", "pulseaudio",
}
_PROTECTED_RE = re.compile(
    r"(jarvis|python|systemd|dbus|xorg|xwayland|cinnamon|gnome-shell|"
    r"gnome-session|plasmashell|kwin|mutter|marco|xfwm|xfce4-session|"
    r"lightdm|gdm|sddm|pipewire|pulseaudio|wireplumber|session|shell)",
    re.I,
)


def _protected_pids():
    """PIDs of Jarvis itself and every ancestor (the terminal/shell chain)."""
    pids = {1}
    try:
        proc = psutil.Process(os.getpid())
        for ancestor in proc.parents():
            pids.add(ancestor.pid)
    except psutil.Error:
        pass
    pids.add(os.getpid())
    return pids


def _is_protected(name, pid=None):
    if pid is not None and pid in _protected_pids():
        return True
    if not name:
        return False
    base = name.lower().split("/")[-1]
    if base in PROTECTED_NAMES:
        return True
    return bool(_PROTECTED_RE.search(base))


# --------------------------------------------------------------------------- #
# desktop application index
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
    """Cached list of installed :class:`App` entries from .desktop files."""
    global _INDEX
    if _INDEX is not None and not refresh:
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


def _launch_app(app):
    """Launch via gtk-launch, then gio launch, then the Exec binary."""
    if has("gtk-launch") and _spawn(["gtk-launch", app.desktop_id]):
        return True
    if has("gio") and _spawn(["gio", "launch", str(app.path)]):
        return True
    if app.exec_bin and shutil.which(app.exec_bin):
        return _spawn([app.exec_bin])
    return False


def open_app(name):
    """Launch an installed app by fuzzy name. Never guesses."""
    app, score = match_app(name)
    if app is None or score < STRONG_MATCH:
        return "I couldn't find that app."
    if _launch_app(app):
        return f"Opening {app.name}."
    return f"I couldn't launch {app.name}."


def _window_titles():
    if is_wayland() or not has("wmctrl"):
        return []
    rc, out = _run(["wmctrl", "-l"])
    if rc != 0:
        return []
    titles = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            titles.append(parts[3].strip())
    return titles


def _wmctrl_close(name):
    """Ask the window manager to close windows whose title matches ``name``."""
    if is_wayland() or not has("wmctrl"):
        return False
    closed = False
    target = name.lower()
    for title in _window_titles():
        low = title.lower()
        if target and (target in low or low in target):
            if _run(["wmctrl", "-c", title])[0] == 0:
                closed = True
    return closed


def _app_tokens(app):
    tokens = set()
    if app.exec_bin:
        tokens.add(app.exec_bin.lower())
    for word in _normalize(app.name).split():
        if len(word) >= 3:
            tokens.add(word)
    if app.desktop_id:
        tokens.add(app.desktop_id.split(".")[-1].lower())
    return tokens


def _matching_pids(app):
    """PIDs whose executable name matches the app's binary/name tokens."""
    tokens = _app_tokens(app)
    protected = _protected_pids()
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
        except psutil.Error:
            continue
        pid = info.get("pid")
        pname = (info.get("name") or "").lower()
        if pid in protected or not pname:
            continue
        if pname in tokens or any(
            t and (pname == t or pname.startswith(t + ".")) for t in tokens
        ):
            pids.append(pid)
    return pids


def _terminate(pids, timeout=3.0):
    """SIGTERM the PIDs, wait, then SIGKILL survivors. Returns count signalled."""
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.Error:
            continue
    if not procs:
        return 0
    for proc in procs:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    return len(procs)


def close_app(app):
    """Gracefully close an app, escalating to SIGKILL after ~3 s."""
    if _is_protected(app.exec_bin) or _is_protected(app.name):
        return f"I won't close {app.name} -- it is protected."
    closed_windows = _wmctrl_close(app.name)
    pids = _matching_pids(app)
    terminated = _terminate(pids) if pids else 0
    if closed_windows or terminated:
        return f"Closed {app.name}."
    return f"I don't see {app.name} running."


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


def matching_process_pids(name, limit=200):
    """PIDs of non-protected processes whose name contains ``name``.

    Used by the ``kill process`` tool; protected processes (the session, the
    shell chain, Jarvis itself) are never returned.
    """
    query = _normalize(name)
    if not query:
        return []
    protected = _protected_pids()
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
        except psutil.Error:
            continue
        pid = info.get("pid")
        pname = info.get("name") or ""
        if pid in protected or not pname or _is_protected(pname, pid):
            continue
        if query in _normalize(pname):
            pids.append(pid)
            if len(pids) >= limit:
                break
    return pids


_TERMINALS = (
    ("gnome-terminal", ["--"]),
    ("kgx", ["--"]),
    ("tilix", ["-e"]),
    ("x-terminal-emulator", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("konsole", ["-e"]),
    ("alacritty", ["-e"]),
    ("xterm", ["-e"]),
)


def run_in_terminal(command):
    """Open a visible terminal and run ``command`` there, leaving it open.

    The caller (see tools.fn_run_in_terminal) applies the same guardrails as
    hidden execution before reaching here.
    """
    command = (command or "").strip()
    if not command:
        return "Which command should I run?"
    inner = f"{command}; echo; echo '[Jarvis] finished -- press Enter to close'; read"
    for name, flag in _TERMINALS:
        if has(name) and _spawn([name, *flag, "bash", "-c", inner]):
            return f"Running in a terminal: {command}"
    return "I couldn't find a terminal emulator."


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

# Spoken-request extraction. STT frequently returns a whole messy sentence for
# "play X on YouTube"; only the song/video name should reach yt-dlp.
# Spoken-request extraction. STT often returns a whole messy sentence for
# "play X on YouTube"; only the song/video name should reach yt-dlp. Crucially,
# words that can be part of a real title ("the", "like", "hey", "just", "now",
# "on", "song", ...) are NEVER stripped on their own -- "Hey Jude" must not
# become "jude". Only unambiguous STT noise and clear filler *phrases*
# ("the song", "music player") are removed.
_PLAY_VERB = re.compile(r"\b(play|watch|listen to|listen)\b", re.I)

# STT noise that is extremely unlikely to be part of a song/video title.
_MEDIA_NOISE = re.compile(
    r"\b(what do you mean|i mean|i meant|you know|i am|i'm|im|"
    r"please|jarvis|uh+|um+|hmm+)\b",
    re.I,
)

# Filler *phrases* (an article + a media noun, or "<medium> player"). These are
# removed as a unit so a bare "the song"/"some music" disappears, while a title
# like "The Nights" or "Like a Prayer" is left alone.
_MEDIA_FILLER_PHRASE = re.compile(
    r"\b(?:just\s+)?(?:the|a|an|some|any|this|that)\s+"
    r"(?:song|songs|music|video|videos|track|tracks|tune|tunes|player|media|clip)s?\b"
    r"|\b(?:music|media|video)\s+player\b",
    re.I,
)

# Words that carry no song information by themselves. If only these remain,
# there is no song to play, so the caller is asked "What should I play?".
_MEDIA_FILLER_WORD = re.compile(
    r"\b(the|a|an|this|that|some|any|my|"
    r"song|songs|music|video|videos|track|tracks|tune|tunes|player|media|clip|"
    r"on|to|like|now|just|hey|ok|okay|please|jarvis|it|me|i|am|you|youtube)\b",
    re.I,
)


def extract_media_query(text):
    """Pull just the song/video name out of a spoken "play ... on YouTube".

    Drops the play/watch/listen verb, a trailing "on YouTube", unambiguous STT
    noise ("what do you mean", "i am", "please", ...) and filler phrases like
    "the song" / "music player". Words that could belong to a title are kept, so
    "Hey Jude", "Like a Prayer" and "The Nights" survive. Returns "" when only
    filler is left, so the caller can ask "What should I play?".
    """
    if not text:
        return ""
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


def _yt_url_search(query):
    return f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"


def _window_has_input_focus(wid):
    """True when ``wid`` really owns the X input focus (X11)."""
    if not wid:
        return False
    rc, current = _run(["xdotool", "getwindowfocus"])
    return rc == 0 and current.strip() == str(wid)


def _reuse_youtube_tab(url):
    """Type ``url`` into the address bar of the already-open YouTube tab (X11).

    A browser opens a *new* tab for every ``open_url``, which is exactly what
    the user dislikes: "I already have YouTube open -- use that window". So
    when a visible YouTube window exists we focus it, press ctrl+l (address
    bar), type the url and hit Return, replacing that tab in place.

    This is only ever done with VERIFIED input focus: Cinnamon's focus-stealing
    prevention can silently refuse activation, and typing into whatever
    *actually* has focus is unacceptable (it could be any application). The
    window is activated, ``windowfocus`` is our fallback, and if the focus
    probe still does not confirm the window we refuse to type and the caller
    falls back to opening a normal tab. Returns False for anything except a
    confirmed navigation.
    """
    if is_wayland() or not has("xdotool") or not url:
        return False
    wid = _youtube_window()
    if not wid or _run(["xdotool", "windowactivate", "--sync", str(wid)])[0] != 0:
        return False
    time.sleep(0.3)
    if not _window_has_input_focus(wid):
        _run(["xdotool", "windowfocus", str(wid)])
        time.sleep(0.3)
        if not _window_has_input_focus(wid):
            _dbg(f"window {wid} does not have input focus; refusing to type")
            return False
    before = _run(["xdotool", "getwindowname", str(wid)])[1].strip()
    _run(["xdotool", "key", "--clearmodifiers", "ctrl+l"])
    time.sleep(0.2)
    _run(["xdotool", "type", "--", url])
    time.sleep(0.15)
    rc, _ = _run(["xdotool", "key", "--clearmodifiers", "Return"])
    time.sleep(2.5)
    after = _run(["xdotool", "getwindowname", str(wid)])[1].strip()
    # The page title must change: anything else means the keys never landed.
    if rc != 0 or after == before:
        _dbg("navigation did not reach the browser; falling back")
        return False
    return True


def open_youtube():
    """Open YouTube -- or bring the existing YouTube tab to the front."""
    wid = _youtube_window()
    if wid and _run(["xdotool", "windowactivate", "--sync", str(wid)])[0] == 0 \
            and _window_has_input_focus(wid):
        return "YouTube is already open -- I brought it up."
    if _open_url("https://www.youtube.com"):
        return "Opening YouTube."
    return "I couldn't open the browser."


def search_youtube(query):
    if not query:
        return open_youtube()
    if _reuse_youtube_tab(_yt_url_search(query)):
        return f"Searching YouTube for {query}."
    if _open_url(_yt_url_search(query)):
        return f"Here are the YouTube results for {query}."
    return "I couldn't open the browser."


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def play_youtube(query):
    """Play the top yt-dlp result for a song/video name; else open the results.

    The argument may be a raw spoken sentence; only the song/video name is used.

    Uses ``--flat-playlist --print id`` so yt-dlp only resolves the search hit's
    id -- it never tries to pick a video format (which fails on YouTube with
    older yt-dlp builds and would silently downgrade us to a results page).
    """
    query = extract_media_query(query)
    if not query:
        return "What should I play?"
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
            if _reuse_youtube_tab(watch_url):
                # Playing in the tab the user already had open. Autoplay is
                # normally blocked without a user gesture, but our typed
                # address bar keypresses *are* gestures -- nudge anyway, in the
                # background, so the spoken reply is never delayed.
                threading.Thread(target=ensure_browser_playing,
                                 daemon=True).start()
                return f"Playing {query} in your open YouTube tab."
            if _open_url(watch_url):
                # Autoplay is normally blocked for a freshly opened tab, which
                # would leave the video paused with no MPRIS session; nudge it in
                # the background so the spoken reply is never delayed.
                threading.Thread(target=ensure_browser_playing,
                                 daemon=True).start()
                return f"Playing {query} on YouTube."
        elif rc != 0:
            # Surface yt-dlp's own error instead of failing silently.
            _dbg(f"yt-dlp failed (rc={rc}): {out}")
    # yt-dlp missing or failed: open the results page and say so.
    reason = "yt-dlp is not installed" if not has("yt-dlp") else "I couldn't find a video"
    _open_url(_yt_url_search(query))
    return f"{reason}, so I opened the YouTube results for {query}."


def close_youtube():
    """Close the YouTube tab, wherever the browser currently is (X11).

    The visible YouTube window is targeted directly: it is activated and
    ctrl+w closes that tab, with focus restored to whatever was there before
    (``_press_in_window``). Only when no YouTube tab is visible do we fall
    back to the focused window -- never guessing outside a browser.
    """
    if is_wayland() or not has("xdotool"):
        return "Which window?"
    wid = _youtube_window()
    if wid and _press_in_window(wid, "ctrl+w"):
        return "Closing the YouTube tab."
    # Fallback for titles without the " - YouTube" suffix: only close the
    # focused browser tab when a browser media session is live -- Chromium only
    # publishes MPRIS for the tab that is playing, so then that tab *is* the
    # one to close. A stopped browser is never touched.
    if _browser_media_player():
        rc, active = _run(["xdotool", "getactivewindow", "getwindowname"])
        if rc == 0 and active and \
                re.search(r"(youtube|chrome|chromium|firefox|brave|edge)", active, re.I):
            rc, _ = _run(["xdotool", "key", "--clearmodifiers", "ctrl+w"])
            if rc == 0:
                return "Closing the YouTube tab."
    return "There is no YouTube tab open."


# --------------------------------------------------------------------------- #
# media (playerctl)
# --------------------------------------------------------------------------- #

# A browser's MPRIS session is deliberately limited: Chromium/Brave report
# CanGoNext/CanGoPrevious = false, so playerctl refuses those outright. When we
# can see the YouTube window we can drive the page with its own shortcuts
# instead (these need no MPRIS at all).
_BROWSER_MEDIA = re.compile(r"brave|chrome|chromium|firefox|edge|vivaldi|opera", re.I)
_YT_KEYS = {"next": "shift+n", "previous": "shift+p", "prev": "shift+p"}


def _browser_media_player():
    """True when playerctl can see a browser publishing a media session.

    A browser only appears here once it is actually playing, which makes this a
    reliable "did the video start?" probe.
    """
    rc, out = _run(["playerctl", "-l"])
    return bool(rc == 0 and _BROWSER_MEDIA.search(out))


def _youtube_window():
    """Window id of a visible browser window showing YouTube, or ``None``."""
    if is_wayland() or not has("xdotool"):
        return None
    rc, out = _run(["xdotool", "search", "--onlyvisible", "--name", "YouTube"])
    if rc != 0:
        return None
    ids = [line.strip() for line in out.splitlines() if line.strip()]
    return ids[-1] if ids else None


def _press_in_window(wid, key, restore=True):
    """Focus ``wid``, send ``key``, then restore the previous focus (X11).

    Chromium ignores synthetic key events sent to an unfocused window, so the
    window genuinely has to own input focus. ``windowactivate`` is asked first,
    ``windowfocus`` (a direct input-focus set that bypasses Cinnamon's
    focus-stealing prevention) is the fallback, and the focus is *verified*
    before any key is sent -- keys are never sprayed blind into whatever else
    may be focused. Returns False whenever the key could not be delivered.
    """
    if not wid or is_wayland() or not has("xdotool"):
        return False
    rc, current = _run(["xdotool", "getactivewindow"])
    previous = current.strip() if rc == 0 else ""
    if _run(["xdotool", "windowactivate", "--sync", str(wid)])[0] != 0:
        return False
    time.sleep(0.3)
    if not _window_has_input_focus(wid):
        _run(["xdotool", "windowfocus", str(wid)])
        time.sleep(0.3)
        if not _window_has_input_focus(wid):
            _dbg(f"window {wid} never took input focus; key not sent")
            return False
    rc, _ = _run(["xdotool", "key", "--clearmodifiers", key])
    time.sleep(0.2)
    if restore and previous and previous != str(wid):
        _run(["xdotool", "windowactivate", "--sync", previous])
    return rc == 0


def ensure_browser_playing(timeout=9.0):
    """Nudge a freshly opened YouTube tab to actually start playing.

    A browser refuses unmuted autoplay until the *site* has seen a user gesture,
    and a spoken command is not one -- so a video we just opened sits paused and
    publishes no MPRIS session at all (which is why "pause" then reports that
    nothing is playing). Best effort, X11 only, never raises.
    """
    if is_wayland() or not (has("xdotool") and has("playerctl")):
        return False
    deadline = time.monotonic() + max(1.0, float(timeout))
    nudged = False
    while time.monotonic() < deadline:
        if _browser_media_player():
            return True
        if not nudged:
            wid = _youtube_window()
            if wid and _press_in_window(wid, "k", restore=False):
                nudged = True
        time.sleep(0.8)
    return _browser_media_player()


_MEDIA = {
    "pause": "pause", "resume": "play", "play": "play",
    "next": "next", "previous": "previous", "prev": "previous",
    "stop": "stop", "toggle": "play-pause",
}


def media_control(action):
    """Pause/resume/next/previous/stop the active player via playerctl."""
    key = (action or "").strip().lower()
    if key not in _MEDIA:
        return "I can handle pause, resume, next, previous or stop."
    if not has("playerctl"):
        return ("playerctl is not installed. To install it, run: "
                "sudo apt install playerctl")
    rc, out = _run(["playerctl", _MEDIA[key]])
    label = "previous" if key == "prev" else key
    if rc == 0:
        return f"Media {label}."
    # A browser's MPRIS has no next/previous track (Chromium/Brave report
    # CanGoNext = false), so playerctl refuses. Drive the page itself instead.
    youtube_key = _YT_KEYS.get(key)
    if youtube_key and _press_in_window(_youtube_window(), youtube_key):
        return f"Media {label}."
    if youtube_key and re.search(r"no player could handle", out, re.I):
        return "That player can't skip tracks."
    return "There is no media player to control."


def media_seek(seconds):
    """Seek the active player forward/back by ``seconds`` (can be negative)."""
    try:
        delta = int(seconds)
    except (TypeError, ValueError):
        return "How many seconds should I seek?"
    if not has("playerctl"):
        return ("playerctl is not installed. To install it, run: "
                "sudo apt install playerctl")
    if delta == 0:
        return "How many seconds should I seek?"
    # playerctl takes the direction as a suffix ("10+" forward, "10-" back); a
    # leading "-10" would be read as an option flag instead of a value.
    offset = f"{abs(delta)}{'+' if delta > 0 else '-'}"
    rc, _ = _run(["playerctl", "position", offset])
    if rc != 0:
        return "There is no media player to control."
    return f"Seeked {'forward' if delta > 0 else 'back'} {abs(delta)} seconds."


def now_playing():
    """Describe the current track, or report that nothing is playing."""
    if not has("playerctl"):
        return ("playerctl is not installed. To install it, run: "
                "sudo apt install playerctl")
    rc, out = _run(["playerctl", "metadata", "--format",
                    "{{artist}} - {{title}} ({{status}})"])
    if rc != 0 or not out.strip():
        return "There is no media player to control."
    return out.strip()


def pause_media_if_playing():
    """Best-effort pause of active media so it does not leak into the mic.

    Returns an opaque resume token, or ``None`` when nothing was playing (or the
    tools are missing). Never raises.
    """
    try:
        if has("playerctl"):
            rc, out = _run(["playerctl", "status"])
            if rc == 0 and out.strip().lower() == "playing":
                if _run(["playerctl", "pause"])[0] == 0:
                    return ("playerctl",)
        if has("pactl"):
            rc, out = _run(["pactl", "list", "sink-inputs"])
            if rc == 0 and re.search(r"State:\s*RUNNING", out):
                if _run(["pactl", "suspend-sink", "@DEFAULT_SINK@", "1"])[0] == 0:
                    return ("pactl",)
    except Exception:
        return None
    return None


def resume_media(token):
    """Best-effort resume after :func:`pause_media_if_playing`. Never raises."""
    if not token:
        return
    try:
        if token[0] == "playerctl":
            _run(["playerctl", "play"])
        elif token[0] == "pactl":
            _run(["pactl", "suspend-sink", "@DEFAULT_SINK@", "0"])
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# volume (pactl) / brightness (brightnessctl)
# --------------------------------------------------------------------------- #

def _clamp(value, low=0, high=100):
    return max(low, min(high, value))


_AUDIO_FAIL = "I couldn't reach the audio system."
_BRIGHT_FAIL = "I couldn't change the brightness."


def _sink_volume():
    rc, out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    if rc == 0 and out:
        match = re.search(r"(\d+)%", out)
        if match:
            return int(match.group(1))
    return None


def volume_control(action, value=None):
    """Volume up/down/set/mute/unmute via pactl, clamped to 0-100."""
    key = (action or "").strip().lower()
    if not has("pactl"):
        return "pactl is not installed."
    sink = "@DEFAULT_SINK@"
    if key in ("mute",):
        rc, _ = _run(["pactl", "set-sink-mute", sink, "1"])
        return "Muted." if rc == 0 else _AUDIO_FAIL
    if key in ("unmute",):
        rc, _ = _run(["pactl", "set-sink-mute", sink, "0"])
        return "Unmuted." if rc == 0 else _AUDIO_FAIL
    if key in ("up", "increase"):
        rc, _ = _run(["pactl", "set-sink-volume", sink, "+10%"])
        return "Volume up." if rc == 0 else _AUDIO_FAIL
    if key in ("down", "decrease"):
        rc, _ = _run(["pactl", "set-sink-volume", sink, "-10%"])
        return "Volume down." if rc == 0 else _AUDIO_FAIL
    if key in ("set", "to", "level") and value is not None:
        pct = _clamp(int(value))
        rc, _ = _run(["pactl", "set-sink-volume", sink, f"{pct}%"])
        return f"Volume set to {pct} percent." if rc == 0 else _AUDIO_FAIL
    return "I can change the volume up, down, mute, unmute or to a percent."


def brightness_control(action, value=None):
    """Brightness up/down/set via brightnessctl, clamped to 1-100."""
    key = (action or "").strip().lower()
    if not has("brightnessctl"):
        return ("brightnessctl is not installed. To install it, run: "
                "sudo apt install brightnessctl")
    if key in ("up", "increase"):
        rc, _ = _run(["brightnessctl", "set", "+10%"])
        return "Brightness up." if rc == 0 else _BRIGHT_FAIL
    if key in ("down", "decrease"):
        rc, _ = _run(["brightnessctl", "set", "10%-"])
        return "Brightness down." if rc == 0 else _BRIGHT_FAIL
    if key in ("set", "to", "level") and value is not None:
        pct = _clamp(int(value), 1, 100)
        rc, _ = _run(["brightnessctl", "set", f"{pct}%"])
        return f"Brightness set to {pct} percent." if rc == 0 else _BRIGHT_FAIL
    return "I can change the brightness up, down or to a percent."


# --------------------------------------------------------------------------- #
# screenshot / lock
# --------------------------------------------------------------------------- #

def capture_screenshot(path):
    """Capture the whole screen to ``path``. Returns the Path on success, else None.

    Tries the *silent* tools first -- ``scrot``, then ImageMagick's ``import`` --
    and only falls back to ``gnome-screenshot``, which plays a bright shutter
    animation on Cinnamon and has no flag to disable it. That matters because
    :func:`vision.act` captures the screen once per step. Never raises. Used by
    :func:`screenshot` and by :mod:`vision` to feed the screen to the vision model.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    attempts = (
        ["scrot", str(path)],
        ["import", "-window", "root", str(path)],
        ["gnome-screenshot", "-f", str(path)],      # last resort: it flashes
    )
    for cmd in attempts:
        if not has(cmd[0]):
            continue
        rc, _ = _run(cmd, timeout=15)
        try:
            ok = rc == 0 and path.exists() and path.stat().st_size > 0
        except OSError:
            ok = False
        if ok:
            return path
    return None


def screenshot():
    """Save a screenshot to ~/Pictures and return the file name."""
    folder = Path.home() / "Pictures"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"jarvis-screenshot-{stamp}.png"
    if capture_screenshot(path) is not None:
        return f"Screenshot saved as {path.name}."
    return "I couldn't take a screenshot; install scrot or ImageMagick."


def lock_screen():
    """Lock the session using whatever locker the desktop provides."""
    for cmd in (
        ["cinnamon-screensaver-command", "-l"],
        ["gnome-screensaver-command", "-l"],
        ["xdg-screensaver", "lock"],
        ["loginctl", "lock-session"],
    ):
        if has(cmd[0]) and _run(cmd)[0] == 0:
            return "Locking the screen."
    return "I couldn't find a screen locker."


# --------------------------------------------------------------------------- #
# screen / window interaction (xdotool + wmctrl; X11 only)
# --------------------------------------------------------------------------- #
#
# Everything here is an explicit, whitelisted action -- Jarvis can move the
# pointer, click, type and press shortcuts, but there is no way for speech to
# smuggle in arbitrary shell. On Wayland these tools do not work, so every
# function degrades to a plain "not supported" message instead of guessing.

_WAYLAND_MSG = ("I can't control the screen on Wayland -- window tools "
                "(wmctrl/xdotool) only work on X11.")


def _xdo(*args, timeout=6):
    """Run an xdotool command. Returns ``(rc, out)``; rc 127 when unavailable."""
    if is_wayland():
        return 127, _WAYLAND_MSG
    if not has("xdotool"):
        return 127, "xdotool is not installed."
    return _run(["xdotool", *args], timeout=timeout)


def screen_size():
    """Return ``(width, height)`` of the display, or ``None`` if unknown."""
    rc, out = _run(["xdotool", "getdisplaygeometry"])
    if rc != 0:
        return None
    parts = out.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def move_mouse(x, y):
    """Move the pointer to absolute ``(x, y)`` pixels."""
    x, y = int(x), int(y)
    rc, out = _xdo("mousemove", str(x), str(y))
    return f"Moved the pointer to {x}, {y}." if rc == 0 else out


def click_at(x, y, button=1, double=False):
    """Click at absolute ``(x, y)``. ``button`` 1=left, 2=middle, 3=right."""
    x, y = int(x), int(y)
    try:
        button = int(button)
    except (TypeError, ValueError):
        button = 1
    if button not in (1, 2, 3):
        button = 1
    cmd = ["mousemove", str(x), str(y), "click"]
    if double:
        cmd += ["--repeat", "2", "--delay", "120"]
    cmd.append(str(button))
    rc, out = _xdo(*cmd)
    if rc != 0:
        return out
    label = {1: "Left", 2: "Middle", 3: "Right"}[button]
    return f"{'Double-' if double else ''}{label}-clicked at {x}, {y}."


def type_text(text):
    """Type ``text`` into whatever window currently has focus."""
    text = "" if text is None else str(text)
    if not text:
        return "I have nothing to type."
    rc, out = _xdo("type", "--clearmodifiers", "--delay", "12", text)
    return "Typed it." if rc == 0 else out


_KEY_OK = re.compile(r"^[A-Za-z0-9+_\- ]{1,60}$")


def press_keys(keys):
    """Press a keyboard shortcut or key, e.g. ``ctrl+s``, ``alt+tab``, ``super``."""
    keys = (keys or "").strip().lower()
    if not _KEY_OK.match(keys):
        return "That key combination looks invalid."
    rc, out = _xdo("key", "--clearmodifiers", keys)
    return f"Pressed {keys}." if rc == 0 else out


def scroll(direction, amount=3):
    """Scroll the focused window: direction is 'up' or 'down'."""
    direction = (direction or "down").strip().lower()
    if direction not in ("up", "down", "left", "right"):
        return "Scroll direction must be up, down, left or right."
    try:
        count = max(1, min(20, int(amount)))
    except (TypeError, ValueError):
        count = 3
    button = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
    rc, out = _xdo("click", "--repeat", str(count), str(button))
    return f"Scrolled {direction}." if rc == 0 else out


def _wmctrl(action, name):
    """Run a wmctrl window action. Returns a normalised (rc, out)."""
    if is_wayland():
        return 127, _WAYLAND_MSG
    if not has("wmctrl"):
        return 127, "wmctrl is not installed."
    return _run(["wmctrl", action, name])


def focus_window(name):
    """Bring the window whose title matches ``name`` to the front."""
    rc, out = _wmctrl("-a", name)
    return f"Focused {name}." if rc == 0 else out


def minimize_window(name):
    """Minimise windows whose title matches ``name``."""
    rc, out = _wmctrl("-r", name)
    if rc != 0:
        return out
    rc, out = _run(["wmctrl", "-r", name, "-b", "add,hidden"])
    return f"Minimized {name}." if rc == 0 else out


def maximize_window(name):
    """Maximise windows whose title matches ``name``."""
    rc, out = _wmctrl("-r", name)
    if rc != 0:
        return out
    rc, out = _run(["wmctrl", "-r", name,
                    "-b", "add,maximized_vert,maximized_horz"])
    return f"Maximized {name}." if rc == 0 else out


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
# Restricted to well-known TLDs so a filename like "notes.txt" is not mistaken
# for a website.
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
        if has("xdg-open") and _spawn(["xdg-open", path]):
            return f"Opening {path}."
        return f"I couldn't find a way to open {path}."
    # A bare domain opens in the browser; anything else goes to xdg-open.
    if _DOMAIN_RE.match(target):
        url = "https://" + target
        return f"Opening {url}." if _open_url(url) else "I couldn't open the browser."
    if has("xdg-open") and _spawn(["xdg-open", path]):
        return f"Handing {path} to the desktop."
    return f"I couldn't find {target}."


# Common folders a spoken name can mean. Kept small and explicit so "open
# downloads" opens the real folder offline, while "open files" still opens the
# Files application.
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
    """Return an existing path for a spoken folder name, or ``None``.

    ``resolve_folder`` only ever returns a path that really exists, so the router
    can safely fall back to application matching when this returns ``None``.
    """
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


def notify(message, title="Jarvis"):
    """Show a desktop notification."""
    message = (message or "").strip()
    if not message:
        return "What should the notification say?"
    if not has("notify-send"):
        return "notify-send is not installed."
    if _spawn(["notify-send", title or "Jarvis", message]):
        return "Notification sent."
    return "I couldn't send a notification."


def clipboard(action):
    """Copy / cut / paste in the focused window, or read the clipboard text."""
    action = (action or "").strip().lower()
    shortcut = {"copy": "ctrl+c", "cut": "ctrl+x", "paste": "ctrl+v",
                "select all": "ctrl+a"}
    if action in shortcut:
        return press_keys(shortcut[action])
    if action in ("read", "get", "show", "what", "contents", "text"):
        if has("xclip"):
            rc, out = _run(["xclip", "-selection", "clipboard", "-o"])
            text = out if rc == 0 else ""
        elif has("xsel"):
            rc, out = _run(["xsel", "--clipboard", "--output"])
            text = out if rc == 0 else ""
        else:
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
    """Read the clipboard with Qt (PyQt5) when xclip/xsel are missing.

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
    query = (query or "").strip()
    if not query:
        return "What file are you looking for?"
    home = os.path.expanduser("~")
    try:
        limit = max(1, min(40, int(limit)))
    except (TypeError, ValueError):
        limit = 15
    hits = []
    if has("plocate"):
        rc, out = _run(["plocate", "-i", "-l", str(limit), query], timeout=10)
    elif has("locate"):
        rc, out = _run(["locate", "-i", "-l", str(limit), query], timeout=10)
    else:
        rc, out = _run(
            ["find", home, "-maxdepth", "5", "-iname", f"*{query}*"],
            timeout=12,
        )
    if rc == 0:
        hits = [line.strip() for line in out.splitlines() if line.strip()]
    if not hits:
        return f"I couldn't find any file matching {query}."
    home_hits = [h for h in hits if h.startswith(home)]
    shown = (home_hits or hits)[:limit]
    return "\n".join(shown)



# --------------------------------------------------------------------------- #
# power (only ever called after a spoken confirmation)
# --------------------------------------------------------------------------- #

def power(kind):
    """Run a confirmed power action: shutdown / restart / logout / suspend."""
    kind = (kind or "").strip().lower()
    if kind in ("shutdown", "poweroff", "power off"):
        _run(["systemctl", "poweroff"])
        return "Shutting down."
    if kind in ("restart", "reboot"):
        _run(["systemctl", "reboot"])
        return "Restarting."
    if kind == "suspend":
        _run(["systemctl", "suspend"])
        return "Suspending."
    if kind in ("logout", "log out"):
        for cmd in (
            ["cinnamon-session-quit", "--logout", "--no-prompt"],
            ["gnome-session-quit", "--logout", "--no-prompt"],
            ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")],
        ):
            if cmd[-1] and has(cmd[0]) and _run(cmd)[0] == 0:
                return "Logging out."
    return "I don't know how to do that."
