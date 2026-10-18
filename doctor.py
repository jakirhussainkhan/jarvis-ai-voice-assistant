"""Readiness self-check for Jarvis.

Prints what is installed, what each missing tool costs you, and how to try every
command without a microphone. Read-only: it never changes the system, and never
runs a desktop action.

    ./venv/bin/python doctor.py
    ./venv/bin/python main.py --check     # same report
"""

import os
import shutil

import desktop
from config import API_KEY, BASE_URL, MODEL_NAME

# (tool, required?, what it powers)
_TOOLS = [
    ("arecord", True, "microphone capture (voice mode)"),
    ("aplay", False, "audio playback used by the mic diagnostic"),
    ("wmctrl", False, "closing/focusing windows (X11)"),
    ("pactl", False, "volume up/down/set/mute"),
    ("playerctl", False, "media pause / resume / next / previous"),
    ("brightnessctl", False, "screen brightness"),
    ("xdotool", False, "closing the focused YouTube tab with Ctrl+W (X11)"),
    ("yt-dlp", False, "playing the top YouTube result"),
    ("gtk-launch", False, "launching .desktop apps"),
    ("gio", False, "launching .desktop apps (fallback)"),
    ("gnome-screenshot", False, "screenshots"),
    ("scrot", False, "screenshots (fallback)"),
    ("import", False, "screenshots (ImageMagick fallback)"),
    ("systemctl", False, "shutdown / restart / suspend"),
    ("loginctl", False, "lock screen / log out"),
]


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
    out.append(f"Endpoint: {BASE_URL}")
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
    out.append("")

    if missing:
        out.append("Enable the missing features with:")
        out.append("  sudo apt install " + " ".join(missing))
        out.append("")

    out.append("Try every command without a microphone:")
    out.append("  JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py")
    return "\n".join(out)


def main():
    print(report())


if __name__ == "__main__":
    main()
