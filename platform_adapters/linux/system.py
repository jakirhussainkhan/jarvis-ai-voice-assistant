"""Linux system adapter: media, brightness, apps, power and the desktop shell.

Every command built here is fixed and whitelisted -- Jarvis never uses ``sudo``
and never runs arbitrary shell from speech.
"""

import os
import re
from pathlib import Path

from platform_adapters import common
from platform_adapters.base import SystemController

# playerctl actions, plus the YouTube page's own shortcuts for players whose
# MPRIS cannot skip tracks (Chromium/Brave report CanGoNext = false).
_MEDIA = {
    "pause": "pause", "resume": "play", "play": "play",
    "next": "next", "previous": "previous", "prev": "previous",
    "stop": "stop", "toggle": "play-pause",
}
_YT_KEYS = {"next": "shift+n", "previous": "shift+p", "prev": "shift+p"}
# Keys used to drive the browser's own player when playerctl cannot help:
# space toggles play/pause, shift+n skips to the next video on YouTube.
_BROWSER_KEYS = {"pause": "space", "resume": "space", "toggle": "space"}

# Terminal emulators tried in order, with the flag that runs a command.
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

_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|io|dev|app|ai|co|me|info|xyz|"
    r"tv|site|online|store|blog|tech|news|live|cloud|uk|us|ca|de|fr|in|jp|au|"
    r"eu)(?:/\S*)?$",
    re.I,
)


def _xrandr_display(text=None):
    """Name of the primary (or first) connected output from ``xrandr``.

    Parsing ``xrandr`` for a line containing ``" connected"`` mirrors the usual
    ``xrandr | grep " connected"`` recipe. Pass ``text`` to reuse an existing
    ``xrandr`` dump (and to keep tests offline).
    """
    if text is None:
        rc, text = common.run(["xrandr"])
        if rc != 0:
            return None
    primary = first = None
    for line in (text or "").splitlines():
        if " connected" not in line:
            continue
        name = line.split()[0]
        if first is None:
            first = name
        if " primary" in line:
            primary = name
    return primary or first


def _xrandr_percent(text=None):
    """Current xrandr brightness (0.0-1.0 multiplier) as a percent, or None."""
    if text is None:
        rc, text = common.run(["xrandr", "--verbose"])
        if rc != 0:
            return None
    match = re.search(r"Brightness:\s*([0-9]*\.?[0-9]+)", text or "")
    if not match:
        return None
    try:
        return max(1, min(100, round(float(match.group(1)) * 100)))
    except ValueError:
        return None


def _xrandr_set(display, percent):
    """Set ``display`` brightness to ``percent`` via xrandr; True on success."""
    value = max(1, min(100, int(percent))) / 100.0
    rc, _ = common.run(
        ["xrandr", "--output", display, "--brightness", f"{value:.2f}"])
    return rc == 0


class LinuxSystemController(SystemController):
    """System actions on Linux (X11/PulseAudio/PipeWire, systemd)."""

    brightness_backend = "brightnessctl"

    def __init__(self, window=None):
        # The window controller is used to close an app's windows first.
        self._window = window

    # -- applications ------------------------------------------------------- #
    def launch_app(self, app):
        if common.has("gtk-launch") and common.spawn(["gtk-launch", app.desktop_id]):
            return True
        if common.has("gio") and common.spawn(["gio", "launch", str(app.path)]):
            return True
        if app.exec_bin and common.has(app.exec_bin):
            return common.spawn([app.exec_bin])
        return False

    def close_app(self, app):
        if common.is_protected(getattr(app, "exec_bin", "")) \
                or common.is_protected(getattr(app, "name", "")):
            return False
        closed_windows = (self._window.close_by_title(app.name)
                          if self._window is not None else False)
        pids = common.matching_pids(app)
        terminated = common.terminate(pids) if pids else 0
        return bool(closed_windows or terminated)

    # -- media (playerctl) -------------------------------------------------- #
    def media_control(self, action, page_fallback=None):
        key = (action or "").strip().lower()
        if key not in _MEDIA:
            return "I can handle pause, resume, next, previous or stop."
        if not common.has("playerctl"):
            return ("playerctl is not installed. To install it, run: "
                    "sudo apt install playerctl")
        rc, out = common.run(["playerctl", _MEDIA[key]])
        label = "previous" if key == "prev" else key
        if rc == 0:
            return f"Media {label}."
        # playerctl could not handle it: drive the browser window directly.
        # Play/pause uses space; next/previous use YouTube's own shortcuts.
        page_key = _BROWSER_KEYS.get(key) or _YT_KEYS.get(key)
        if page_key and page_fallback is not None and page_fallback(page_key):
            return f"Media {label}."
        if key in _YT_KEYS and re.search(r"no player could handle", out, re.I):
            return "That player can't skip tracks."
        return "There is no media player to control."

    def media_seek(self, seconds):
        try:
            delta = int(seconds)
        except (TypeError, ValueError):
            return "How many seconds should I seek?"
        if delta == 0:
            return "How many seconds should I seek?"
        if not common.has("playerctl"):
            return ("playerctl is not installed. To install it, run: "
                    "sudo apt install playerctl")
        # playerctl takes the direction as a suffix ("10+" forward, "10-" back).
        offset = f"{abs(delta)}{'+' if delta > 0 else '-'}"
        rc, _ = common.run(["playerctl", "position", offset])
        if rc != 0:
            return "There is no media player to control."
        return f"Seeked {'forward' if delta > 0 else 'back'} {abs(delta)} seconds."

    def now_playing(self):
        if not common.has("playerctl"):
            return ("playerctl is not installed. To install it, run: "
                    "sudo apt install playerctl")
        rc, out = common.run(["playerctl", "metadata", "--format",
                              "{{artist}} - {{title}} ({{status}})"])
        if rc != 0 or not out.strip():
            return "There is no media player to control."
        return out.strip()

    def media_players(self):
        rc, out = common.run(["playerctl", "-l"])
        return out if rc == 0 else ""

    def media_available(self):
        return common.has("playerctl")

    def pause_media_if_playing(self):
        try:
            if common.has("playerctl"):
                rc, out = common.run(["playerctl", "status"])
                if rc == 0 and out.strip().lower() == "playing":
                    if common.run(["playerctl", "pause"])[0] == 0:
                        return ("playerctl",)
            if common.has("pactl"):
                rc, out = common.run(["pactl", "list", "sink-inputs"])
                if rc == 0 and re.search(r"State:\s*RUNNING", out):
                    if common.run(["pactl", "suspend-sink",
                                   "@DEFAULT_SINK@", "1"])[0] == 0:
                        return ("pactl",)
        except Exception:
            return None
        return None

    def resume_media(self, token):
        if not token:
            return
        try:
            if token[0] == "playerctl":
                common.run(["playerctl", "play"])
            elif token[0] == "pactl":
                common.run(["pactl", "suspend-sink", "@DEFAULT_SINK@", "0"])
        except Exception:
            pass

    # -- brightness (brightnessctl, with xrandr fallback) ------------------- #
    def get_brightness(self):
        rc, out = common.run(["brightnessctl", "--machine-readable", "get"])
        if rc != 0 or not out.strip():
            return None
        try:
            current, maximum = (int(part) for part in out.strip().split(",", 1))
            return round(current * 100 / maximum) if maximum else None
        except (ValueError, ZeroDivisionError):
            return None

    def set_brightness(self, percent):
        rc, _ = common.run(["brightnessctl", "set", f"{int(percent)}%"])
        return rc == 0

    def brightness_control(self, action, value=None):
        key = (action or "").strip().lower()
        if key not in ("up", "increase", "down", "decrease", "set", "to",
                       "level"):
            return "I can change the brightness up, down or to a percent."
        if key in ("set", "to", "level") and value is None:
            # "brightness" with no number: ask rather than fall through to
            # int(None) in the xrandr path.
            return "I can change the brightness up, down or to a percent."
        if common.has("brightnessctl"):
            if key in ("up", "increase"):
                rc, _ = common.run(["brightnessctl", "set", "+10%"])
                if rc == 0:
                    return "Brightness up."
            elif key in ("down", "decrease"):
                rc, _ = common.run(["brightnessctl", "set", "10%-"])
                if rc == 0:
                    return "Brightness down."
            elif value is not None:
                pct = max(1, min(100, int(value)))
                rc, _ = common.run(["brightnessctl", "set", f"{pct}%"])
                if rc == 0:
                    return f"Brightness set to {pct} percent."
            # brightnessctl failed (usually a permissions error): fall through
            # to xrandr, which works on the X server without special rights.
        return self._brightness_xrandr(key, value)

    def _brightness_xrandr(self, key, value):
        """Software brightness via ``xrandr --brightness`` (0.0-1.0; 1.0 = 100%)."""
        if not common.has("xrandr"):
            return ("brightnessctl is not installed. To install it, run: "
                    "sudo apt install brightnessctl")
        display = _xrandr_display()
        if not display:
            return "I couldn't find a connected display."
        if key in ("up", "increase"):
            percent = min(100, (_xrandr_percent() or 50) + 10)
        elif key in ("down", "decrease"):
            percent = max(1, (_xrandr_percent() or 50) - 10)
        else:
            percent = max(1, min(100, int(value)))
        if _xrandr_set(display, percent):
            if key in ("up", "increase"):
                return "Brightness up."
            if key in ("down", "decrease"):
                return "Brightness down."
            return f"Brightness set to {percent} percent."
        return "I couldn't change the brightness."

    # -- screenshots / lock ------------------------------------------------- #
    def screenshot(self, path):
        """Capture the whole screen to ``path``. Returns the Path or None.

        Silent tools first (`scrot`, then ImageMagick `import`); `gnome-screenshot`
        only as a last resort because it flashes the screen.
        """
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        attempts = (
            ["scrot", str(path)],
            ["import", "-window", "root", str(path)],
            ["gnome-screenshot", "-f", str(path)],
        )
        for cmd in attempts:
            if not common.has(cmd[0]):
                continue
            rc, _ = common.run(cmd, timeout=15)
            try:
                ok = rc == 0 and path.exists() and path.stat().st_size > 0
            except OSError:
                ok = False
            if ok:
                return path
        return None

    def lock_screen(self):
        for cmd in (
            ["cinnamon-screensaver-command", "-l"],
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ):
            if common.has(cmd[0]) and common.run(cmd)[0] == 0:
                return "Locking the screen."
        return "I couldn't find a screen locker."

    # -- notifications / files / clipboard ---------------------------------- #
    def notify(self, message, title="Jarvis"):
        message = (message or "").strip()
        if not message:
            return "What should the notification say?"
        if not common.has("notify-send"):
            return "notify-send is not installed."
        if common.spawn(["notify-send", title or "Jarvis", message]):
            return "Notification sent."
        return "I couldn't send a notification."

    def open_path(self, path):
        if common.has("xdg-open") and common.spawn(["xdg-open", path]):
            if os.path.exists(path):
                return f"Opening {path}."
            return f"Handing {path} to the desktop."
        if os.path.exists(path):
            return f"I couldn't find a way to open {path}."
        return f"I couldn't find {path}."

    def read_clipboard(self):
        if common.has("xclip"):
            rc, out = common.run(["xclip", "-selection", "clipboard", "-o"])
            return out if rc == 0 else None
        if common.has("xsel"):
            rc, out = common.run(["xsel", "--clipboard", "--output"])
            return out if rc == 0 else None
        return None

    def find_files(self, query, limit=15):
        query = (query or "").strip()
        if not query:
            return "What file are you looking for?"
        home = os.path.expanduser("~")
        try:
            limit = max(1, min(40, int(limit)))
        except (TypeError, ValueError):
            limit = 15
        hits = []
        if common.has("plocate"):
            rc, out = common.run(["plocate", "-i", "-l", str(limit), query], timeout=10)
        elif common.has("locate"):
            rc, out = common.run(["locate", "-i", "-l", str(limit), query], timeout=10)
        else:
            rc, out = common.run(
                ["find", home, "-maxdepth", "5", "-iname", f"*{query}*"], timeout=12)
        if rc == 0:
            hits = [line.strip() for line in out.splitlines() if line.strip()]
        if not hits:
            return f"I couldn't find any file matching {query}."
        home_hits = [h for h in hits if h.startswith(home)]
        return "\n".join((home_hits or hits)[:limit])

    def open_terminal(self):
        """Open the default terminal emulator as an empty window.

        Reuses the same ordered :data:`_TERMINALS` list as
        :meth:`run_in_terminal`, so whichever terminal the user has installed is
        the one that opens.
        """
        for name, _flag in _TERMINALS:
            if common.has(name) and common.spawn([name]):
                return True
        return False

    def run_in_terminal(self, command):
        command = (command or "").strip()
        if not command:
            return "Which command should I run?"
        inner = f"{command}; echo; echo '[Jarvis] finished -- press Enter to close'; read"
        for name, flag in _TERMINALS:
            if common.has(name) and common.spawn([name, *flag, "bash", "-c", inner]):
                return f"Running in a terminal: {command}"
        return "I couldn't find a terminal emulator."

    # -- power -------------------------------------------------------------- #
    def power(self, kind):
        kind = (kind or "").strip().lower()
        if kind in ("shutdown", "poweroff", "power off"):
            common.run(["systemctl", "poweroff"])
            return "Shutting down."
        if kind in ("restart", "reboot"):
            common.run(["systemctl", "reboot"])
            return "Restarting."
        if kind == "suspend":
            common.run(["systemctl", "suspend"])
            return "Suspending."
        if kind in ("logout", "log out"):
            for cmd in (
                ["cinnamon-session-quit", "--logout", "--no-prompt"],
                ["gnome-session-quit", "--logout", "--no-prompt"],
                ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")],
            ):
                if cmd[-1] and common.has(cmd[0]) and common.run(cmd)[0] == 0:
                    return "Logging out."
        return "I don't know how to do that."
