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
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import psutil

_DEVNULL = subprocess.DEVNULL

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


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

# Spoken-request extraction. STT frequently returns a whole messy sentence for
# "play X on YouTube"; only the song/video name should reach yt-dlp.
_PLAY_VERB = re.compile(r"\b(play|watch|listen to)\b", re.I)
_MEDIA_FILLER = re.compile(
    r"\b(what do you mean|you know the song|you know|"
    r"please|just|meant|mean|i am|i'm|im|"
    r"on youtube|youtube|"
    r"song|songs|music|video|videos|track|tracks|tune|tunes|player|media|clip|"
    r"the|a|an|some|any|on|like|jarvis|now|hey|okay|ok)\b",
    re.I,
)


def extract_media_query(text):
    """Pull just the song/video name out of a spoken "play ... on YouTube".

    Takes the text after an explicit play/watch/listen verb, drops a trailing
    "on YouTube" and strips spoken filler / STT noise ("please", "the song",
    "music", "just", "what do you mean", "i am", ...). Returns "" when nothing
    usable is left, so the caller can ask "What should I play?".
    """
    if not text:
        return ""
    match = _PLAY_VERB.search(text)
    tail = text[match.end():] if match else text
    # Strip a trailing "on youtube" (and any other "youtube" mention).
    tail = re.sub(r"\bon\s+youtube\b", " ", tail, flags=re.I)
    tail = re.sub(r"\byoutube\b", " ", tail, flags=re.I)
    tail = _MEDIA_FILLER.sub(" ", tail)
    return re.sub(r"\s+", " ", tail).strip(" .,!?;:-'\" ")


def _yt_url_search(query):
    return f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"


def open_youtube():
    if _open_url("https://www.youtube.com"):
        return "Opening YouTube."
    return "I couldn't open the browser."


def search_youtube(query):
    if not query:
        return open_youtube()
    if _open_url(_yt_url_search(query)):
        return f"Here are the YouTube results for {query}."
    return "I couldn't open the browser."


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def play_youtube(query):
    """Play the top yt-dlp result for a song/video name; else open the results.

    The argument may be a raw spoken sentence; only the song/video name is used.
    """
    query = extract_media_query(query)
    if not query:
        return "What should I play?"
    if has("yt-dlp"):
        rc, out = _run(
            ["yt-dlp", f"ytsearch1:{query}", "--get-id", "--no-warnings", "-q"],
            timeout=25,
        )
        vid = ""
        for line in out.splitlines():
            line = line.strip()
            if _YT_ID.match(line):
                vid = line
        if rc == 0 and vid:
            if _open_url(f"https://www.youtube.com/watch?v={vid}"):
                return f"Playing {query} on YouTube."
    # yt-dlp missing or failed: open the results page and say so.
    reason = "yt-dlp is not installed" if not has("yt-dlp") else "I couldn't find a video"
    _open_url(_yt_url_search(query))
    return f"{reason}, so I opened the YouTube results for {query}."


def close_youtube():
    """Close the current browser tab (ctrl+w) only when it is focused.

    Needs ``xdotool`` and X11. Otherwise we cannot safely target the tab, so we
    ask which window instead of closing the whole browser.
    """
    if is_wayland() or not has("xdotool"):
        return "Which window?"
    rc, active = _run(["xdotool", "getactivewindow", "getwindowname"])
    if rc != 0 or not active:
        return "Which window?"
    if re.search(r"(youtube|chrome|chromium|firefox|brave|edge)", active, re.I):
        rc, _ = _run(["xdotool", "key", "--clearmodifiers", "ctrl+w"])
        if rc == 0:
            return "Closing the YouTube tab."
    return "Which window?"


# --------------------------------------------------------------------------- #
# media (playerctl)
# --------------------------------------------------------------------------- #

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
        return "playerctl is not installed. Install it with: sudo apt install playerctl."
    rc, out = _run(["playerctl", _MEDIA[key]])
    if rc != 0:
        return "There is no media player to control."
    return f"Media {key if key != 'prev' else 'previous'}."


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
        _run(["pactl", "set-sink-mute", sink, "1"])
        return "Muted."
    if key in ("unmute",):
        _run(["pactl", "set-sink-mute", sink, "0"])
        return "Unmuted."
    if key in ("up", "increase"):
        _run(["pactl", "set-sink-volume", sink, "+10%"])
        return "Volume up."
    if key in ("down", "decrease"):
        _run(["pactl", "set-sink-volume", sink, "-10%"])
        return "Volume down."
    if key in ("set", "to", "level") and value is not None:
        pct = _clamp(int(value))
        _run(["pactl", "set-sink-volume", sink, f"{pct}%"])
        return f"Volume set to {pct} percent."
    return "I can change the volume up, down, mute, unmute or to a percent."


def brightness_control(action, value=None):
    """Brightness up/down/set via brightnessctl, clamped to 1-100."""
    key = (action or "").strip().lower()
    if not has("brightnessctl"):
        return ("brightnessctl is not installed. Install it with: "
                "sudo apt install brightnessctl.")
    if key in ("up", "increase"):
        _run(["brightnessctl", "set", "+10%"])
        return "Brightness up."
    if key in ("down", "decrease"):
        _run(["brightnessctl", "set", "10%-"])
        return "Brightness down."
    if key in ("set", "to", "level") and value is not None:
        pct = _clamp(int(value), 1, 100)
        _run(["brightnessctl", "set", f"{pct}%"])
        return f"Brightness set to {pct} percent."
    return "I can change the brightness up, down or to a percent."


# --------------------------------------------------------------------------- #
# screenshot / lock
# --------------------------------------------------------------------------- #

def screenshot():
    """Save a screenshot to ~/Pictures and return the file name."""
    folder = Path.home() / "Pictures"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"jarvis-screenshot-{stamp}.png"

    if has("gnome-screenshot"):
        rc, _ = _run(["gnome-screenshot", "-f", str(path)], timeout=15)
        if rc == 0 and path.exists():
            return f"Screenshot saved as {path.name}."
    if has("scrot"):
        rc, _ = _run(["scrot", str(path)], timeout=15)
        if rc == 0 and path.exists():
            return f"Screenshot saved as {path.name}."
    if has("import"):
        rc, _ = _run(["import", "-window", "root", str(path)], timeout=15)
        if rc == 0 and path.exists():
            return f"Screenshot saved as {path.name}."
    return "I couldn't take a screenshot; install gnome-screenshot or scrot."


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
