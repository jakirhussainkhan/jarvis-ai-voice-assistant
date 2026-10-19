"""Cross-platform adapters for Jarvis (the Platform Adapter Pattern).

``platform.system()`` is detected once at import time and the matching
implementation subpackage is loaded. The three singletons are exported directly
so callers stay OS-agnostic::

    from platform_adapters import Audio, Window, System
    Audio.set_volume(40)
    System.brightness_control("up")

Use :func:`get_adapters` to build a fresh set for a specific OS (mainly tests)::

    audio, window, system = get_adapters("Windows")
"""

import platform as _platform

from platform_adapters.base import (  # noqa: F401  (re-exported for typing)
    AudioController,
    SystemController,
    WindowController,
)

__all__ = [
    "Audio", "Window", "System",
    "AudioController", "WindowController", "SystemController",
    "get_adapters",
]


def get_adapters(system=None):
    """Return ``(audio, window, system)`` controllers for ``system``.

    ``system`` defaults to :func:`platform.system` (``"Linux"``, ``"Windows"``,
    ``"Darwin"``). Anything unrecognised falls back to the Linux adapters.
    """
    name = (system if system is not None else _platform.system()).strip().lower()
    if name.startswith("win"):
        from platform_adapters.windows import build
    elif name in ("darwin", "mac", "macos", "osx"):
        from platform_adapters.macos import build
    else:
        from platform_adapters.linux import build
    return build()


Audio, Window, System = get_adapters()
