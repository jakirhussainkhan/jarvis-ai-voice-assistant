"""macOS platform adapters (sounddevice/osascript/pywinctl)."""

from platform_adapters.macos.audio import MacOSAudioController
from platform_adapters.macos.system import MacOSSystemController
from platform_adapters.macos.window import MacOSWindowController

__all__ = ["MacOSAudioController", "MacOSWindowController", "MacOSSystemController",
           "build"]


def build():
    """Return ``(audio, window, system)`` controllers wired together."""
    window = MacOSWindowController()
    return MacOSAudioController(), window, MacOSSystemController(window=window)
