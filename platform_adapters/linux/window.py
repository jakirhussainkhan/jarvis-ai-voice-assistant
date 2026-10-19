"""Linux window adapter: ``wmctrl`` for windows and ``xdotool`` for automation.

Both tools are X11-only. On a Wayland session they either do nothing or cannot
see the windows, so every operation degrades to a plain "not supported" reply
instead of guessing.
"""

import re
import time

from platform_adapters import common
from platform_adapters.base import WindowController

_WAYLAND_MSG = ("I can't control the screen on Wayland -- window tools "
                "(wmctrl/xdotool) only work on X11.")

# A key expression we are willing to hand to xdotool.
_KEY_OK = re.compile(r"^[A-Za-z0-9+_\- ]{1,60}$")

_SCROLL_BUTTON = {"up": 4, "down": 5, "left": 6, "right": 7}


class LinuxWindowController(WindowController):
    """Windows and screen automation via wmctrl/xdotool (X11)."""

    # -- low-level helpers -------------------------------------------------- #
    def _xdo(self, *args, timeout=6):
        """Run an xdotool command. Returns ``(rc, out)``; rc 127 when unusable."""
        if common.is_wayland():
            return 127, _WAYLAND_MSG
        if not common.has("xdotool"):
            return 127, "xdotool is not installed."
        return common.run(["xdotool", *args], timeout=timeout)

    def _wmctrl(self, action, name):
        """Run a wmctrl window action. Returns a normalised ``(rc, out)``."""
        if common.is_wayland():
            return 127, _WAYLAND_MSG
        if not common.has("wmctrl"):
            return 127, "wmctrl is not installed."
        return common.run(["wmctrl", action, name])

    # -- enumeration / focus ------------------------------------------------ #
    def list_window_titles(self):
        if common.is_wayland() or not common.has("wmctrl"):
            return []
        rc, out = common.run(["wmctrl", "-l"])
        if rc != 0:
            return []
        titles = []
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4:
                titles.append(parts[3].strip())
        return titles

    def get_active_window(self):
        rc, out = self._xdo("getactivewindow")
        return out.strip() if rc == 0 and out.strip() else None

    def get_active_window_title(self):
        rc, out = self._xdo("getactivewindow", "getwindowname")
        return out.strip() if rc == 0 else ""

    def locate_window(self, name):
        rc, out = self._xdo("search", "--onlyvisible", "--name", str(name))
        if rc != 0:
            return None
        ids = [line.strip() for line in out.splitlines() if line.strip()]
        return ids[-1] if ids else None

    def window_name(self, handle):
        rc, out = self._xdo("getwindowname", str(handle))
        return out.strip() if rc == 0 else ""

    def activate_window(self, handle):
        rc, _ = self._xdo("windowactivate", "--sync", str(handle))
        return rc == 0

    def activate_by_title(self, name):
        rc, out = self._wmctrl("-a", name)
        return f"Focused {name}." if rc == 0 else out

    def close_by_title(self, name):
        if common.is_wayland() or not common.has("wmctrl"):
            return False
        closed = False
        target = (name or "").lower()
        for title in self.list_window_titles():
            low = title.lower()
            if target and (target in low or low in target):
                if common.run(["wmctrl", "-c", title])[0] == 0:
                    closed = True
        return closed

    def locate_by_class(self, keywords):
        """Handles of windows whose WM_CLASS contains any of ``keywords``.

        ``wmctrl -lx`` prints the WM_CLASS in the third column
        (e.g. ``gnome-terminal-server.Gnome-terminal``). This finds terminal
        windows whose titles are just the shell prompt, so they cannot be matched
        by name.
        """
        keys = [str(k).lower() for k in (keywords or []) if k]
        if not keys or common.is_wayland() or not common.has("wmctrl"):
            return []
        rc, out = common.run(["wmctrl", "-lx"])
        if rc != 0:
            return []
        handles = []
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 3:
                continue
            handle, _desktop, wm_class = parts[0], parts[1], parts[2]
            low = wm_class.lower()
            if any(key in low for key in keys):
                handles.append(handle)
        return handles

    def close_by_class(self, keywords):
        """Close every window matching :meth:`locate_by_class`.

        ``wmctrl -ic <id>`` asks the window manager to close it gracefully;
        ``xdotool windowkill`` is the fallback when that fails.
        """
        handles = self.locate_by_class(keywords)
        if not handles:
            return False
        closed = False
        for handle in handles:
            if common.run(["wmctrl", "-ic", handle])[0] == 0:
                closed = True
                continue
            if common.has("xdotool") and common.run(
                    ["xdotool", "windowkill", handle])[0] == 0:
                closed = True
        return closed

    def minimize_by_title(self, name):
        rc, out = self._wmctrl("-r", name)
        if rc != 0:
            return out
        rc, out = common.run(["wmctrl", "-r", name, "-b", "add,hidden"])
        return f"Minimized {name}." if rc == 0 else out

    def maximize_by_title(self, name):
        rc, out = self._wmctrl("-r", name)
        if rc != 0:
            return out
        rc, out = common.run(
            ["wmctrl", "-r", name, "-b", "add,maximized_vert,maximized_horz"])
        return f"Maximized {name}." if rc == 0 else out

    def has_input_focus(self, handle):
        if not handle:
            return False
        rc, current = common.run(["xdotool", "getwindowfocus"])
        return rc == 0 and current.strip() == str(handle)

    def force_focus(self, handle):
        if not handle or common.is_wayland() or not common.has("xdotool"):
            return False
        return common.run(["xdotool", "windowfocus", str(handle)])[0] == 0

    def send_key(self, handle, key, restore=True):
        """Focus ``handle``, send ``key``, restore the previous focus (X11).

        Chromium ignores synthetic key events sent to an unfocused window, so the
        window genuinely has to own input focus. The focus is *verified* before
        any key is sent, so keys are never sprayed blind into another app.
        """
        if not handle or common.is_wayland() or not common.has("xdotool"):
            return False
        rc, current = common.run(["xdotool", "getactivewindow"])
        previous = current.strip() if rc == 0 else ""
        if common.run(["xdotool", "windowactivate", "--sync",
                       str(handle)])[0] != 0:
            return False
        time.sleep(0.3)
        if not self.has_input_focus(handle):
            self.force_focus(handle)
            time.sleep(0.3)
            if not self.has_input_focus(handle):
                return False
        ok = self.send_keys(key)
        time.sleep(0.2)
        if restore and previous and previous != str(handle):
            common.run(["xdotool", "windowactivate", "--sync", previous])
        return ok

    def send_keys(self, keys):
        rc, _ = self._xdo("key", "--clearmodifiers", keys)
        return rc == 0

    def type_string(self, text):
        rc, _ = self._xdo("type", "--clearmodifiers", "--delay", "12", str(text))
        return rc == 0

    # -- pointer / keyboard ------------------------------------------------- #
    def move_mouse(self, x, y):
        x, y = int(x), int(y)
        rc, out = self._xdo("mousemove", str(x), str(y))
        return f"Moved the pointer to {x}, {y}." if rc == 0 else out

    def click(self, x, y, button=1, double=False):
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
        rc, out = self._xdo(*cmd)
        if rc != 0:
            return out
        label = {1: "Left", 2: "Middle", 3: "Right"}[button]
        return f"{'Double-' if double else ''}{label}-clicked at {x}, {y}."

    def type_text(self, text):
        text = "" if text is None else str(text)
        if not text:
            return "I have nothing to type."
        rc, out = self._xdo("type", "--clearmodifiers", "--delay", "12", text)
        return "Typed it." if rc == 0 else out

    def press_keys(self, keys):
        keys = (keys or "").strip().lower()
        if not _KEY_OK.match(keys):
            return "That key combination looks invalid."
        rc, out = self._xdo("key", "--clearmodifiers", keys)
        return f"Pressed {keys}." if rc == 0 else out

    def scroll(self, direction, amount=3):
        direction = (direction or "down").strip().lower()
        if direction not in _SCROLL_BUTTON:
            return "Scroll direction must be up, down, left or right."
        try:
            count = max(1, min(20, int(amount)))
        except (TypeError, ValueError):
            count = 3
        button = _SCROLL_BUTTON[direction]
        rc, out = self._xdo("click", "--repeat", str(count), str(button))
        return f"Scrolled {direction}." if rc == 0 else out

    def screen_size(self):
        rc, out = common.run(["xdotool", "getdisplaygeometry"])
        if rc != 0:
            return None
        parts = out.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return None
        return int(parts[0]), int(parts[1])

    def can_send_keys(self):
        return not common.is_wayland() and common.has("xdotool")
