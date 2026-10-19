"""macOS system adapter: ``osascript`` for apps/media and native shell tools."""

import os

from platform_adapters import common
from platform_adapters.base import SystemController


class MacOSSystemController(SystemController):
    """System actions on macOS (osascript, open, screencapture, pbcopy)."""

    brightness_backend = "brightness"

    def __init__(self, window=None):
        self._window = window

    def _osascript(self, script):
        rc, out = common.run(["osascript", "-e", script])
        return out.strip() if rc == 0 else None

    # -- applications ------------------------------------------------------- #
    def launch_app(self, app):
        name = getattr(app, "name", "") or getattr(app, "exec_bin", "")
        if not name:
            return False
        if common.spawn(["open", "-a", name]):
            return True
        path = getattr(app, "path", None)
        return bool(path and common.spawn(["open", str(path)]))

    def close_app(self, app):
        if common.is_protected(getattr(app, "exec_bin", "")) \
                or common.is_protected(getattr(app, "name", "")):
            return False
        name = getattr(app, "name", "")
        quit_ok = False
        if name:
            quit_ok = self._osascript(
                f'tell application "{name}" to quit') is not None
        closed_windows = (self._window.close_by_title(name)
                          if self._window is not None else False)
        pids = common.matching_pids(app)
        terminated = common.terminate(pids) if pids else 0
        return bool(quit_ok or closed_windows or terminated)

    # -- media (osascript against Music / Spotify) -------------------------- #
    def _player_command(self, verb):
        for player in ("Music", "Spotify"):
            if self._osascript(f'tell application "{player}" to {verb}') is not None:
                return True
        return False

    def media_control(self, action, page_fallback=None):
        key = (action or "").strip().lower()
        verb = {
            "pause": "pause", "resume": "play", "play": "play",
            "next": "next track", "previous": "previous track",
            "prev": "previous track", "stop": "stop", "toggle": "playpause",
        }.get(key)
        if verb is None:
            return "I can handle pause, resume, next, previous or stop."
        if not self._player_command(verb):
            return "There is no media player to control."
        label = "previous" if key == "prev" else key
        return f"Media {label}."

    def media_seek(self, seconds):
        try:
            delta = int(seconds)
        except (TypeError, ValueError):
            return "How many seconds should I seek?"
        if delta == 0:
            return "How many seconds should I seek?"
        if self._player_command(f"set player position to (player position + {delta})"):
            return f"Seeked {'forward' if delta > 0 else 'back'} {abs(delta)} seconds."
        return "There is no media player to control."

    def now_playing(self):
        for player in ("Music", "Spotify"):
            out = self._osascript(
                f'tell application "{player}" to '
                'name of current track & " - " & artist of current track')
            if out:
                return out
        return "There is no media player to control."

    def media_players(self):
        names = self._osascript(
            'tell application "System Events" to get name of every process '
            'whose background only is false')
        return (names or "").replace(", ", "\n")

    def media_available(self):
        return True

    def pause_media_if_playing(self):
        if self._osascript('tell application "Music" to player state as string') == "playing":
            if self._player_command("pause"):
                return ("osascript",)
        return None

    def resume_media(self, token):
        if token:
            self._player_command("play")

    # -- brightness --------------------------------------------------------- #
    def get_brightness(self):
        if not common.has("brightness"):
            return None
        rc, out = common.run(["brightness", "-l"])
        try:
            return round(float(out) * 100)
        except (TypeError, ValueError):
            return None

    def set_brightness(self, percent):
        if not common.has("brightness"):
            return False
        return common.run(["brightness", str(max(0.01, min(1.0, int(percent) / 100.0)))])[0] == 0

    def brightness_control(self, action, value=None):
        if not common.has("brightness"):
            print("[platform] Brightness control needs the 'brightness' CLI "
                  "(brew install brightness)")
            return "brightness is not installed."
        key = (action or "").strip().lower()
        current = self.get_brightness() or 50
        if key in ("up", "increase"):
            return "Brightness up." if self.set_brightness(current + 10) \
                else "I couldn't change the brightness."
        if key in ("down", "decrease"):
            return "Brightness down." if self.set_brightness(current - 10) \
                else "I couldn't change the brightness."
        if key in ("set", "to", "level") and value is not None:
            pct = max(1, min(100, int(value)))
            if self.set_brightness(pct):
                return f"Brightness set to {pct} percent."
            return "I couldn't change the brightness."
        return "I can change the brightness up, down or to a percent."

    # -- desktop shell ------------------------------------------------------ #
    def screenshot(self, path):
        rc, _ = common.run(["screencapture", "-x", str(path)], timeout=15)
        return path if rc == 0 else None

    def lock_screen(self):
        script = ('tell application "System Events" to keystroke "q" '
                  'using {control down, command down}')
        if self._osascript(script) is not None:
            return "Locking the screen."
        return "I couldn't lock the screen."

    def notify(self, message, title="Jarvis"):
        message = (message or "").replace('"', "'").strip()
        if not message:
            return "What should the notification say?"
        script = f'display notification "{message}" with title "{title or "Jarvis"}"'
        if self._osascript(script) is not None:
            return "Notification sent."
        return "I couldn't send a notification."

    def open_path(self, path):
        if common.spawn(["open", str(path)]):
            return f"Opening {path}."
        return f"I couldn't open {path}."

    def read_clipboard(self):
        rc, out = common.run(["pbpaste"])
        return out if rc == 0 else None

    def find_files(self, query, limit=15):
        query = (query or "").strip()
        if not query:
            return "What file are you looking for?"
        try:
            limit = max(1, min(40, int(limit)))
        except (TypeError, ValueError):
            limit = 15
        if common.has("mdfind"):
            rc, out = common.run(["mdfind", "-name", query], timeout=10)
            if rc == 0:
                hits = [line.strip() for line in out.splitlines() if line.strip()]
                if hits:
                    return "\n".join(hits[:limit])
        home = os.path.expanduser("~")
        hits = []
        for root_dir, dirs, files in os.walk(home):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if query.lower() in name.lower():
                    hits.append(os.path.join(root_dir, name))
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        if not hits:
            return f"I couldn't find any file matching {query}."
        return "\n".join(hits)

    def run_in_terminal(self, command):
        command = (command or "").strip()
        if not command:
            return "Which command should I run?"
        script = f'tell application "Terminal" to do script "{command}"'
        if self._osascript(script) is not None:
            return f"Running in a terminal: {command}"
        return "I couldn't find a terminal emulator."

    def power(self, kind):
        kind = (kind or "").strip().lower()
        events = {
            "shutdown": ('shut down', "Shutting down."),
            "poweroff": ('shut down', "Shutting down."),
            "restart": ('restart', "Restarting."),
            "reboot": ('restart', "Restarting."),
            "logout": ('log out', "Logging out."),
            "suspend": ('sleep', "Suspending."),
        }
        if kind in events:
            verb, reply = events[kind]
            if self._osascript(f'tell application "System Events" to {verb}') is not None:
                return reply
        return "I don't know how to do that."
