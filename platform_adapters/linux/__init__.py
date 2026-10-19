"""Linux platform adapters (X11/PulseAudio/PipeWire, systemd)."""

from platform_adapters.linux.audio import LinuxAudioController
from platform_adapters.linux.system import LinuxSystemController
from platform_adapters.linux.window import LinuxWindowController

__all__ = ["LinuxAudioController", "LinuxWindowController", "LinuxSystemController",
           "build"]


def build():
    """Return ``(audio, window, system)`` controllers wired together."""
    window = LinuxWindowController()
    return LinuxAudioController(), window, LinuxSystemController(window=window)
