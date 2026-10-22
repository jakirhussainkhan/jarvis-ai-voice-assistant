"""Readiness self-check for Jarvis.

Prints what is installed, what each missing tool costs you, and how to try every
command without a microphone. Read-only: it never changes the system, and never
runs a desktop action.

    ./venv/bin/python doctor.py
    ./venv/bin/python main.py --check     # same report

The Linux section checks the CLI tools Jarvis drives (``arecord``, ``wmctrl``,
...). Windows and macOS use the ``sounddevice``/``pywinctl``/``pycaw`` Python
adapters instead, so they get their own list instead of a misleading "arecord is
missing, run apt install".
"""

import importlib.util
import os
import platform
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

# (import name, what it powers, pip package to install it)
_SOUNDDEVICE_FEATURES = [
    ("pywinctl", "listing/focusing/closing windows", "pywinctl"),
    ("pyautogui", "screen automation: click, type, scroll", "pyautogui"),
    ("PIL", "screenshots for the vision model", "pillow"),
    ("plyer", "desktop notifications", "plyer"),
]

_WINDOWS_FEATURES = [
    ("pycaw", "volume up/down/set/mute", "pycaw comtypes"),
] + _SOUNDDEVICE_FEATURES + [
    ("screen_brightness_control", "screen brightness", "screen-brightness-control"),
]


def _has_module(name):
    """True if a Python module can be imported (without importing it)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _is_windows(system=None):
    """True when ``system`` (default: this machine) is Windows."""
    return (system or platform.system()).strip().lower().startswith("win")


def _is_linux(system=None):
    """True when ``system`` (default: this machine) is Linux."""
    return (system or platform.system()).strip().lower() in (
        "linux", "linux2", "gnu/linux", "posix")


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


def _sounddevice_status(system=None):
    """One line describing microphone capture through PortAudio."""
    if not _has_module("sounddevice"):
        return ("Microphone: MISSING sounddevice -- voice mode needs it, run "
                "'pip install sounddevice'")
    try:
        import sounddevice as sd
        device = sd.query_devices(kind="input")
        return (f"Microphone: OK -- {device['name']} "
                f"({int(device['max_input_channels'])} ch)")
    except Exception:
        # A real failure worth fixing: voice mode cannot work without a device.
        if _is_windows(system):
            hint = ("allow microphone access in Windows Settings > Privacy & "
                    "security > Microphone")
        elif (system or platform.system()).strip().lower() == "darwin":
            hint = ("allow microphone access in System Settings > Privacy & "
                    "Security > Microphone")
        else:
            hint = "check that PulseAudio/PipeWire is running and the mic is not muted"
        return ("Microphone: NONE -- PortAudio found no input device. Plug in a "
                f"microphone and {hint}, then run this check again.")


def _text_mode_hint(system=None):
    """The copy-paste command that tries commands without a microphone."""
    if _is_windows(system):
        return ('  $env:JARVIS_TEXT=1; $env:JARVIS_MUTE=1; '
                '.\\venv\\Scripts\\python.exe main.py')
    return "  JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py"


def _launch_hint(system=None):
    """The copy-paste command that opens the floating widget."""
    if _is_windows(system):
        return '  .\\venv\\Scripts\\python.exe main.py --gui'
    return "  ./venv/bin/python main.py --gui"


def _linux_tools(out):
    """Append the Linux CLI tool table; return the missing pip/apt packages."""
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
    return missing


def _sounddevice_features(out, features):
    """Append a Python-package feature table; return the missing packages."""
    out.append("Features (Python adapters):")
    missing = []
    for module, why, package in features:
        present = _has_module(module)
        out.append(f"  [{'ok ' if present else ' --'}] {module:26s} {why}")
        if not present:
            missing.append(package)
    for tool, why in (("ffmpeg", "media / screenshots / TTS decoding"),
                      ("yt-dlp", "playing the top YouTube result")):
        present = shutil.which(tool) is not None
        out.append(f"  [{'ok ' if present else ' --'}] {tool:26s} {why}")
    return missing


def report(system=None):
    """Return the readiness report as a string.

    ``system`` defaults to :func:`platform.system`; tests pass ``"Linux"`` or
    ``"Windows"`` to check both branches on any machine.
    """
    out = ["Jarvis readiness check", "=" * 46]

    if _is_linux(system):
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
        out.append(f"Session: {session} ({desktop_env})")
        if desktop.is_wayland():
            out.append("  ! Wayland: window tools (wmctrl/xdotool) do not work; "
                       "app/media control falls back to processes/playerctl.")
    else:
        name = system or platform.system()
        out.append(f"Platform: {name} (Python adapters, no X11 CLI tools)")
        out.append(_sounddevice_status(system))

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

    missing = []
    if _is_linux(system):
        missing = _linux_tools(out)
    elif _is_windows(system):
        missing = _sounddevice_features(out, _WINDOWS_FEATURES)
    else:
        missing = _sounddevice_features(out, _SOUNDDEVICE_FEATURES)
    out.append("")

    try:
        apps = desktop.app_index()
        out.append(f"Applications indexed: {len(apps)}")
    except Exception as exc:                     # never fail the whole check
        out.append(f"Applications indexed: error ({exc})")

    qt = _has_module("PyQt5")
    out.append("Floating widget + tray: " + ("PyQt5 present" if qt else
               "PyQt5 MISSING -- install PyQt5 for `main.py --gui`"))
    if _is_linux(system):
        clip = shutil.which("xclip") or shutil.which("xsel")
        out.append("Clipboard read: " + ("xclip/xsel" if clip else
                   ("Qt fallback" if qt else "unavailable")))
    else:
        out.append("Clipboard read: " + ("Qt clipboard" if qt else "unavailable"))
    out.append(_wake_status())
    out.append(f"Auto-start on boot: {startup.status_text()}")
    out.append("")

    if missing:
        if _is_linux(system):
            out.append("Missing optional tools. Copy-paste to install them "
                       "yourself")
            out.append("(Jarvis never runs this for you -- it only prints it):")
            out.append("  sudo apt install " + " ".join(sorted(set(missing))))
        else:
            pip = (".\\venv\\Scripts\\pip.exe" if _is_windows(system)
                   else "./venv/bin/pip")
            out.append("Missing optional packages. Copy-paste to install them "
                       "yourself")
            out.append("(Jarvis never runs this for you -- it only prints it):")
            out.append(f"  {pip} install " + " ".join(sorted(set(missing))))
        out.append("")

    out.append("Try every command without a microphone:")
    out.append(_text_mode_hint(system))
    out.append("Offline only (no network, no agent):")
    out.append("  .\\venv\\Scripts\\python.exe main.py --offline"
               if _is_windows(system)
               else "  JARVIS_MUTE=1 ./venv/bin/python main.py --offline")
    out.append("Ask about the current screen once:")
    out.append('  .\\venv\\Scripts\\python.exe main.py --screen "what is on my screen?"'
               if _is_windows(system)
               else '  ./venv/bin/python main.py --screen "what is on my screen?"')
    out.append("Floating widget + system tray:")
    out.append(_launch_hint(system) + "   # click the orb -- or, once the")
    out.append("       wake word is installed, just say \"Hey Jarvis\" -- to talk;")
    out.append("       continuous chat keeps the mic open after every reply until")
    out.append("       you go quiet (60 s), say \"bye\", press Stop or mute; Stop")
    out.append("       cuts a reply or a pending listen, and the orb reacts to")
    out.append("       your voice and to Jarvis's own speech")
    out.append("Closing the widget hides it to the tray. To stop it from a "
               "terminal:")
    out.append("  .\\venv\\Scripts\\python.exe main.py --quit"
               if _is_windows(system)
               else "  ./venv/bin/python main.py --quit")
    return "\n".join(out)


def main():
    print(report())


if __name__ == "__main__":
    main()
