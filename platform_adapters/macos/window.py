"""macOS window adapter: ``pywinctl`` for windows and ``pyautogui`` for input.

The pywinctl/pyautogui implementation is shared with the Windows adapter (both
libraries are cross-platform); macOS only needs its own fallback wording, which
mentions the accessibility permission pyautogui requires on macOS.
"""

from platform_adapters.windows.window import WindowsWindowController


class MacOSWindowController(WindowsWindowController):
    """Windows are managed with pywinctl; the pointer via pyautogui."""

    missing_windows_msg = "Window control needs pywinctl: pip install pywinctl"
    missing_input_msg = ("Screen control needs pyautogui and the Accessibility "
                         "permission: pip install pyautogui")
