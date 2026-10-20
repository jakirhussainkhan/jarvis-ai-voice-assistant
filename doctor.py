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
import urllib.request
from urllib.error import HTTPError

import desktop
import startup
from config import (
    API_KEY,
    BASE_URL,
    MODEL_NAME,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    USE_LOCAL_LLM,
    VISION_MODEL,
)

# (tool, required?, what it powers, Debian/Ubuntu package to install it)
_TOOLS = [
    ("arecord", True, "microphone capture (voice mode)", "alsa-utils"),
    ("aplay", False, "audio playback used by the mic diagnostic", "alsa-utils"),
    ("wmctrl", False, "focus/minimise/maximise/close windows (X11)", "wmctrl"),
    ("xdotool", False, "screen automation: click, type, scroll, keys (X11)", "xdotool"),
    ("pactl", False, "volume up/down/set/mute", "pulseaudio-utils"),
    ("playerctl", False, "media pause / resume / next / seek", "playerctl"),
    ("brightnessctl", False, "screen brightness", "brightnessctl"),
    ("yt-dlp", False, "playing the top YouTube result", "yt-dlp"),
    ("gtk-launch", False, "launching .desktop apps", "libgtk-3-bin"),
    ("gio", False, "launching .desktop apps (fallback)", "libglib2.0-bin"),
    ("gnome-screenshot", False, "screenshots (last resort only; it flashes)", "gnome-screenshot"),
    ("scrot", False, "screenshots (silent, preferred)", "scrot"),
    ("import", False, "screenshots (silent fallback)", "imagemagick"),
    ("convert", False, "shrinking screenshots for the vision model", "imagemagick"),
    ("ffmpeg", False, "image conversion / media", "ffmpeg"),
    ("notify-send", False, "desktop notifications", "libnotify-bin"),
    ("xdg-open", False, "opening files, folders and URLs", "xdg-utils"),
    ("xclip", False, "reading the clipboard", "xclip"),
    ("xsel", False, "reading the clipboard (fallback)", "xsel"),
    ("systemctl", False, "shutdown / restart / suspend", "systemd"),
    ("loginctl", False, "lock screen / log out", "systemd"),
]


def _has_module(name):
    """True if a Python module can be imported (without importing it)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _ollama_root_url():
    """Return the Ollama server root (without the OpenAI-compatible /v1)."""
    root = OLLAMA_BASE_URL.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root


def _ollama_running():
    """Ping the local Ollama server with a short, hard timeout.

    Any HTTP response (even an error status) means the server is up. Connection
    failures are expected for the optional backend and are never fatal.
    """
    try:
        with urllib.request.urlopen(_ollama_root_url(), timeout=1):
            return True
    except HTTPError:
        return True
    except Exception:
        return False


def _wake_status():
    """One line describing the optional "Hey Jarvis" wake word."""
    try:
        import wake
    except Exception as exc:                      # never fail the whole check
        return f"Wake word: error ({exc})"
    if not wake.available():
        return ("Wake word: OFF -- \"Hey Jarvis\" needs openWakeWord. "
                "Install it yourself with:\n  " + wake.install_hint())
    phrase = wake.model_name()
    if not wake.enabled():
        return (f"Wake word: installed but switched off (JARVIS_WAKE=0) -- "
                f"remove that to listen for \"{phrase}\"")
    return (f"Wake word: ON -- listening for \"{phrase}\" (offline, "
            f"JARVIS_WAKE=0 disables it, 'quit' stops it)")


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
    if USE_LOCAL_LLM:
        out.append(f"Local AI mode: ON -- Ollama {OLLAMA_MODEL} at {OLLAMA_BASE_URL}")
    else:
        out.append("Local AI mode: OFF -- Groq is primary; Ollama is used only "
                   "if the Groq circuit opens")
    if _ollama_running():
        out.append("Ollama (Local AI): OK (Running)")
    else:
        out.append("Ollama (Local AI): Not running (Optional - used only if "
                   "USE_LOCAL_LLM=True)")
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
    for tool, required, why, package in _TOOLS:
        present = shutil.which(tool) is not None
        if present:
            tag = "ok "
        else:
            tag = "REQ" if required else " --"
            if not required:
                missing.append(package)
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
    out.append(_wake_status())
    out.append(f"Auto-start on boot: {startup.status_text()}")
    out.append("")

    if missing:
        out.append("Missing optional tools. Copy-paste to install them yourself")
        out.append("(Jarvis never runs this for you -- it only prints it):")
        out.append("  sudo apt install " + " ".join(sorted(set(missing))))
        out.append("")

    out.append("Try every command without a microphone:")
    out.append("  JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py")
    out.append("Offline only (no network, no agent):")
    out.append("  JARVIS_MUTE=1 ./venv/bin/python main.py --offline")
    out.append("Ask about the current screen once:")
    out.append("  ./venv/bin/python main.py --screen \"what is on my screen?\"")
    out.append("Floating widget + system tray:")
    out.append("  ./venv/bin/python main.py --gui   # click the orb -- or, once the")
    out.append("       wake word is installed, just say \"Hey Jarvis\" -- to talk;")
    out.append("       continuous chat keeps the mic open after every reply until")
    out.append("       you go quiet (60 s), say \"bye\", press Stop or mute; Stop")
    out.append("       cuts a reply or a pending listen, and the orb reacts to")
    out.append("       your voice and to Jarvis's own speech")
    return "\n".join(out)


def main():
    print(report())


if __name__ == "__main__":
    main()
