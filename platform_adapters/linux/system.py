"""Linux system adapter: media, brightness, apps, power and the desktop shell.

Every command built here is fixed and whitelisted -- Jarvis never uses ``sudo``
and never runs arbitrary shell from speech.
"""

import os
import re
from datetime import datetime
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
        youtube_key = _YT_KEYS.get(key)
        if youtube_key and page_fallback is not None and page_fallback(youtube_key):
            return f"Media {label}."
        if youtube_key and re.search(r"no player could handle", out, re.I):
            return "That player can't skip tracks."
        return "There is no media player to control."

    def media_seek(self, seconds):
        try:
            delta = int(seconds)
        except (TypeError, ValueError):
            return "How many seconds should I seek?"
        if not common.has("playerctl"):
            return ("playerctl is not installed. To install it, run: "
                    "sudo apt install playerctl")
        if delta == 0:
            return "How many seconds should I seek?"
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

    # -- brightness (brightnessctl) ----------------------------------------- #
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
        if not common.has("brightnessctl"):
            return ("brightnessctl is not installed. To install it, run: "
                    "sudo apt install brightnessctl")
        if key in ("up", "increase"):
            rc, _ = common.run(["brightnessctl", "set", "+10%"])
            return "Brightness up." if rc == 0 else "I couldn't change the brightness."
        if key in ("down", "decrease"):
            rc, _ = common.run(["brightnessctl", "set", "10%-"])
            return "Brightness down." if rc == 0 else "I couldn't change the brightness."
        if key in ("set", "to", "level") and value is not None:
            pct = max(1, min(100, int(value)))
            rc, _ = common.run(["brightnessctl", "set", f"{pct}%"])
            if rc == 0:
                return f"Brightness set to {pct} percent."
            return "I couldn't change the brightness."
        return "I can change the brightness up, down or to a percent."

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
