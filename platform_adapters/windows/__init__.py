"""Windows platform adapters (sounddevice/pycaw/pywinctl)."""

from platform_adapters.windows.audio import WindowsAudioController
from platform_adapters.windows.system import WindowsSystemController
from platform_adapters.windows.window import WindowsWindowController

__all__ = ["WindowsAudioController", "WindowsWindowController",
           "WindowsSystemController", "build"]


def build():
    """Return ``(audio, window, system)`` controllers wired together."""
    window = WindowsWindowController()
    return WindowsAudioController(), window, WindowsSystemController(window=window)
