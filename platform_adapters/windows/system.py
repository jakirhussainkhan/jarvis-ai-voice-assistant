"""Windows system adapter: apps, media keys, brightness, power and shell.

Uses only the standard library plus optional packages; when an optional package
is missing, the affected action prints a clear install hint and returns a
graceful reply instead of crashing.
"""

import os
import subprocess

from platform_adapters import common
from platform_adapters.base import SystemController

# Virtual-key codes for the multimedia keys.
_VK = {"play": 0xB3, "pause": 0xB3, "toggle": 0xB3, "next": 0xB0, "previous": 0xB1,
       "prev": 0xB1, "stop": 0xB2}


def _warn(feature, package):
    print(f"[platform] {feature} needs {package}: pip install {package}")


class WindowsSystemController(SystemController):
    """System actions on Windows (standard library + optional extras)."""

    brightness_backend = "screen_brightness_control"

    def __init__(self, window=None):
        self._window = window

    # -- applications ------------------------------------------------------- #
    def launch_app(self, app):
        path = getattr(app, "path", None)
        try:
            if path and str(path).lower().endswith(".lnk"):
                os.startfile(str(path))            # noqa: S606
                return True
        except Exception:
            pass
        exe = getattr(app, "exec_bin", "") or ""
        if exe:
            try:
                subprocess.Popen([exe], close_fds=True,
                                 **common.hidden_process_kwargs())
                return True
            except OSError:
                pass
        try:
            os.startfile(getattr(app, "name", "") or str(path))  # noqa: S606
            return True
        except Exception:
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

    # -- media (multimedia keys) -------------------------------------------- #
    def _press_key(self, vk):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 2, 0)
            return True
        except Exception:
            return False

    def media_control(self, action, page_fallback=None):
        key = (action or "").strip().lower()
        if key not in _VK:
            return "I can handle pause, resume, next, previous or stop."
        if not self._press_key(_VK[key]):
            return "I couldn't reach the media controls."
        label = "previous" if key == "prev" else key
        return f"Media {label}."

    def media_seek(self, seconds):
        return "I can't seek media on this platform yet."

    def now_playing(self):
        return "I can't read the current track on this platform yet."

    def media_players(self):
        return ""

    def media_available(self):
        return True

    def pause_media_if_playing(self):
        return ("media-key",) if self._press_key(_VK["pause"]) else None

    def resume_media(self, token):
        if token:
            self._press_key(_VK["play"])

    # -- brightness --------------------------------------------------------- #
    def _get_sbc(self):
        try:
            import screen_brightness_control as sbc
            return sbc
        except Exception:
            return None

    def get_brightness(self):
        sbc = self._get_sbc()
        if sbc is None:
            return None
        try:
            values = sbc.get_brightness()
            return int(values[0]) if isinstance(values, list) else int(values)
        except Exception:
            return None

    def set_brightness(self, percent):
        sbc = self._get_sbc()
        if sbc is None:
            return False
        try:
            sbc.set_brightness(max(1, min(100, int(percent))))
            return True
        except Exception:
            return False

    def brightness_control(self, action, value=None):
        if self._get_sbc() is None:
            _warn("Brightness control", "screen-brightness-control")
            return "screen-brightness-control is not installed."
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
        try:
            from PIL import ImageGrab

            image = ImageGrab.grab()
            image.save(str(path))
            return path
        except Exception:
            _warn("Screenshots", "pillow")
            return None

    def lock_screen(self):
        try:
            import ctypes

            if ctypes.windll.user32.LockWorkStation():
                return "Locking the screen."
        except Exception:
            pass
        return "I couldn't lock the screen."

    def notify(self, message, title="Jarvis"):
        message = (message or "").strip()
        if not message:
            return "What should the notification say?"
        try:
            from plyer import notification

            notification.notify(title=title or "Jarvis", message=message, timeout=8)
            return "Notification sent."
        except Exception:
            _warn("Notifications", "plyer")
            return "I couldn't send a notification."

    def open_path(self, path):
        try:
            os.startfile(str(path))                # noqa: S606
            return f"Opening {path}."
        except Exception:
            return f"I couldn't open {path}."

    def read_clipboard(self):
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        except Exception:
            return None

    def find_files(self, query, limit=15):
        query = (query or "").strip().lower()
        if not query:
            return "What file are you looking for?"
        try:
            limit = max(1, min(40, int(limit)))
        except (TypeError, ValueError):
            limit = 15
        home = os.path.expanduser("~")
        hits = []
        for root_dir, dirs, files in os.walk(home):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if query in name.lower():
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
        if common.spawn(["cmd", "/k", command]):
            return f"Running in a terminal: {command}"
        return "I couldn't open a terminal."

    def power(self, kind):
        kind = (kind or "").strip().lower()
        if kind in ("shutdown", "poweroff", "power off"):
            common.run(["shutdown", "/s", "/t", "0"])
            return "Shutting down."
        if kind in ("restart", "reboot"):
            common.run(["shutdown", "/r", "/t", "0"])
            return "Restarting."
        if kind in ("logout", "log out"):
            common.run(["shutdown", "/l"])
            return "Logging out."
        if kind == "suspend":
            common.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            return "Suspending."
        return "I don't know how to do that."
