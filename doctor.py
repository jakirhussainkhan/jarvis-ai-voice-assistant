"""Readiness self-check for Jarvis.

Prints what is installed, what each missing tool costs you, and how to try every
command without a microphone. Read-only: it never changes the system, and never
runs a desktop action.

    ./venv/bin/python doctor.py
    ./venv/bin/python main.py --check     # same report
"""

import importlib.util
import os
import shutil

import desktop
from config import API_KEY, BASE_URL, MODEL_NAME, VISION_MODEL

# (tool, required?, what it powers)
_TOOLS = [
    ("arecord", True, "microphone capture (voice mode)"),
    ("aplay", False, "audio playback used by the mic diagnostic"),
    ("wmctrl", False, "focus/minimise/maximise/close windows (X11)"),
    ("xdotool", False, "screen automation: click, type, scroll, keys (X11)"),
    ("pactl", False, "volume up/down/set/mute"),
    ("playerctl", False, "media pause / resume / next / seek"),
    ("brightnessctl", False, "screen brightness"),
    ("yt-dlp", False, "playing the top YouTube result"),
    ("gtk-launch", False, "launching .desktop apps"),
    ("gio", False, "launching .desktop apps (fallback)"),
    ("gnome-screenshot", False, "screenshots"),
    ("scrot", False, "screenshots (fallback)"),
    ("import", False, "screenshots (ImageMagick fallback)"),
    ("convert", False, "shrinking screenshots for the vision model"),
    ("ffmpeg", False, "image conversion / media"),
    ("notify-send", False, "desktop notifications"),
    ("xdg-open", False, "opening files, folders and URLs"),
    ("xclip", False, "reading the clipboard"),
    ("xsel", False, "reading the clipboard (fallback)"),
    ("systemctl", False, "shutdown / restart / suspend"),
    ("loginctl", False, "lock screen / log out"),
]


def _has_module(name):
    """True if a Python module can be imported (without importing it)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def report():
    """Return the readiness report as a string."""
    out = ["Jarvis readiness check", "=" * 46]

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    out.append(f"Session: {session} ({desktop_env})")
    if desktop.is_wayland():
        out.append("  ! Wayland: window tools (wmctrl/xdotool) do not work; "
                   "app/media control falls back to processes/playerctl.")

    if API_KEY:
        out.append("GROQ_API_KEY: set")
    else:
        out.append("GROQ_API_KEY: MISSING -- only the offline router will work "
                   "(agent/web answers need a key in .env)")
    out.append(f"Model: {MODEL_NAME}")
    out.append(f"Vision model: {VISION_MODEL}")
    out.append(f"Endpoint: {BASE_URL}")
    out.append("Internet: live search / page fetch / Wikipedia / weather "
               "(keyless, needs a connection)")
    out.append("Offline: apps, folders, windows, media, volume, brightness, "
               "clipboard, screenshots, notes, timers and terminal all work "
               "with no connection (try `main.py --offline`)")
    out.append("Terminal: guarded -- no sudo, no power/session commands, risky "
               "commands are confirmed first")
    out.append("")

    out.append("Tools:")
    missing = []
    for tool, required, why in _TOOLS:
        present = shutil.which(tool) is not None
        if present:
            tag = "ok "
        else:
            tag = "REQ" if required else " --"
            if not required:
                missing.append(tool)
        out.append(f"  [{tag}] {tool:18s} {why}")
    out.append("")

    try:
        apps = desktop.app_index()
        out.append(f"Applications indexed: {len(apps)}")
    except Exception as exc:                     # never fail the whole check
        out.append(f"Applications indexed: error ({exc})")

    qt = _has_module("PyQt5")
    out.append("Floating widget + tray: " + ("PyQt5 present" if qt else
               "PyQt5 MISSING -- install PyQt5 for `main.py --gui`"))
    clip = shutil.which("xclip") or shutil.which("xsel")
    out.append("Clipboard read: " + ("xclip/xsel" if clip else
               ("Qt fallback" if qt else "unavailable")))
    out.append("")

    if missing:
        out.append("Optional extras (install them with your package manager):")
        out.append("  " + " ".join(missing))
        out.append("")

    out.append("Try every command without a microphone:")
    out.append("  JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py")
    out.append("Offline only (no network, no agent):")
    out.append("  JARVIS_MUTE=1 ./venv/bin/python main.py --offline")
    out.append("Ask about the current screen once:")
    out.append("  ./venv/bin/python main.py --screen \"what is on my screen?\"")
    out.append("Floating widget + system tray:")
    out.append("  ./venv/bin/python main.py --gui   # click the orb to talk;")
    out.append("       mic button mutes/unmutes, Stop cuts a reply, the orb")
    out.append("       reacts to your voice and to Jarvis's own speech")
    return "\n".join(out)


def main():
    print(report())


if __name__ == "__main__":
    main()
