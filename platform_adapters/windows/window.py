"""Windows window adapter: ``pywinctl`` for windows and ``pyautogui`` for input."""

from platform_adapters.base import WindowController

def _pywinctl():
    try:
        import pywinctl
        return pywinctl
    except Exception:
        return None


def _pyautogui():
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None


class WindowsWindowController(WindowController):
    """Windows are managed with pywinctl; the pointer via pyautogui."""

    missing_windows_msg = "Window control needs pywinctl: pip install pywinctl"
    missing_input_msg = "Screen control needs pyautogui: pip install pyautogui"

    # -- enumeration / focus ------------------------------------------------ #
    def list_window_titles(self):
        pywinctl = _pywinctl()
        if pywinctl is None:
            print("[platform] " + self.missing_windows_msg)
            return []
        try:
            return [t for t in pywinctl.getAllTitles() if t]
        except Exception:
            return []

    def get_active_window(self):
        pywinctl = _pywinctl()
        if pywinctl is None:
            return None
        try:
            return pywinctl.getActiveWindow()
        except Exception:
            return None

    def get_active_window_title(self):
        window = self.get_active_window()
        return getattr(window, "title", "") or ""

    def locate_window(self, name):
        pywinctl = _pywinctl()
        if pywinctl is None:
            return None
        try:
            matches = pywinctl.getWindowsWithTitle(str(name))
            return matches[-1] if matches else None
        except Exception:
            return None

    def window_name(self, handle):
        return getattr(handle, "title", "") or ""

    def activate_window(self, handle):
        try:
            handle.activate()
            return True
        except Exception:
            return False

    def activate_by_title(self, name):
        window = self.locate_window(name)
        if window is None:
            return f"I couldn't find a window called {name}."
        return f"Focused {name}." if self.activate_window(window) \
            else f"I couldn't focus {name}."

    def close_by_title(self, name):
        pywinctl = _pywinctl()
        if pywinctl is None:
            return False
        closed = False
        try:
            for window in pywinctl.getWindowsWithTitle(str(name)):
                try:
                    window.close()
                    closed = True
                except Exception:
                    pass
        except Exception:
            return False
        return closed

    def minimize_by_title(self, name):
        window = self.locate_window(name)
        if window is None:
            return f"I couldn't find a window called {name}."
        try:
            window.minimize()
            return f"Minimized {name}."
        except Exception:
            return f"I couldn't minimise {name}."

    def maximize_by_title(self, name):
        window = self.locate_window(name)
        if window is None:
            return f"I couldn't find a window called {name}."
        try:
            window.maximize()
            return f"Maximized {name}."
        except Exception:
            return f"I couldn't maximise {name}."

    def has_input_focus(self, handle):
        try:
            return bool(handle.isActive)
        except Exception:
            return False

    def force_focus(self, handle):
        return self.activate_window(handle)

    def send_keys(self, keys):
        pyautogui = _pyautogui()
        if pyautogui is None:
            return False
        try:
            pyautogui.hotkey(*[part.strip() for part in str(keys).split("+")])
            return True
        except Exception:
            return False

    def type_string(self, text):
        pyautogui = _pyautogui()
        if pyautogui is None:
            return False
        try:
            pyautogui.write(str(text), interval=0.012)
            return True
        except Exception:
            return False

    def send_key(self, handle, key, restore=True):
        if handle is None or not self.activate_window(handle):
            return False
        pyautogui = _pyautogui()
        if pyautogui is None:
            return False
        try:
            pyautogui.hotkey(*[part.strip() for part in key.split("+")])
            return True
        except Exception:
            return False

    # -- pointer / keyboard ------------------------------------------------- #
    def move_mouse(self, x, y):
        pyautogui = _pyautogui()
        if pyautogui is None:
            return self.missing_input_msg
        try:
            pyautogui.moveTo(int(x), int(y))
            return f"Moved the pointer to {int(x)}, {int(y)}."
        except Exception as exc:
            return str(exc)

    def click(self, x, y, button=1, double=False):
        pyautogui = _pyautogui()
        if pyautogui is None:
            return self.missing_input_msg
        try:
            button = int(button)
        except (TypeError, ValueError):
            button = 1
        name = {1: "left", 2: "middle", 3: "right"}.get(button, "left")
        try:
            pyautogui.click(int(x), int(y), clicks=2 if double else 1, button=name)
        except Exception as exc:
            return str(exc)
        label = name.capitalize()
        return f"{'Double-' if double else ''}{label}-clicked at {int(x)}, {int(y)}."

    def type_text(self, text):
        text = "" if text is None else str(text)
        if not text:
            return "I have nothing to type."
        pyautogui = _pyautogui()
        if pyautogui is None:
            return self.missing_input_msg
        try:
            pyautogui.write(text, interval=0.012)
            return "Typed it."
        except Exception as exc:
            return str(exc)

    def press_keys(self, keys):
        keys = (keys or "").strip().lower()
        if not keys or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789+_- " for c in keys):
            return "That key combination looks invalid."
        pyautogui = _pyautogui()
        if pyautogui is None:
            return self.missing_input_msg
        try:
            pyautogui.hotkey(*[part.strip() for part in keys.split("+")])
            return f"Pressed {keys}."
        except Exception as exc:
            return str(exc)

    def scroll(self, direction, amount=3):
        direction = (direction or "down").strip().lower()
        if direction not in ("up", "down"):
            return "Scroll direction must be up or down."
        pyautogui = _pyautogui()
        if pyautogui is None:
            return self.missing_input_msg
        try:
            count = max(1, min(20, int(amount)))
        except (TypeError, ValueError):
            count = 3
        try:
            pyautogui.scroll(count if direction == "up" else -count)
            return f"Scrolled {direction}."
        except Exception as exc:
            return str(exc)

    def screen_size(self):
        pyautogui = _pyautogui()
        if pyautogui is not None:
            try:
                return tuple(pyautogui.size())
            except Exception:
                pass
        pywinctl = _pywinctl()
        if pywinctl is not None:
            try:
                return tuple(pywinctl.getScreenSize())
            except Exception:
                pass
        return None

    def can_send_keys(self):
        return _pyautogui() is not None
