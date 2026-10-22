"""Cross-platform "Start on System Boot" support for JARVIS.

The public API is intentionally tiny:

    startup.set_autostart(True)          # install the login entry
    startup.set_autostart(False)         # remove it
    startup.is_autostart_enabled()       # is it currently installed?

Each platform uses its native mechanism:

* Linux  -- ``~/.config/autostart/jarvis.desktop``
* macOS  -- ``~/Library/LaunchAgents/com.jarvis.assistant.plist``
* Windows -- ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``

Everything is per-user: no ``sudo``, no machine-wide registry keys, and no
system directories. All file writes and registry access are reversible.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

APP_NAME = "JARVIS"
APP_ID = "com.jarvis.assistant"

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "JARVIS"


# --- platform / paths ------------------------------------------------------- #
def _platform():
    """Return ``sys.platform``; a seam tests can replace."""
    return sys.platform


def _home(home=None):
    """Return the home directory to install into."""
    return Path(home).expanduser() if home else Path.home()


def linux_autostart_path(home=None):
    """Path of the XDG autostart entry used on Linux."""
    return _home(home) / ".config" / "autostart" / "jarvis.desktop"


def macos_plist_path(home=None):
    """Path of the LaunchAgent property list used on macOS."""
    return _home(home) / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def _root():
    """Directory to run from: the checkout, or the installed executable.

    In a PyInstaller onefile build ``__file__`` points inside the temporary
    extraction directory, which no longer exists after the app exits. The
    autostart entry must instead use the real installation directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _launch_arguments(use_run_sh=True):
    """Return the command that starts the GUI for this installation.

    A frozen build points at its own executable. A source checkout uses
    ``run.sh`` on Linux (the documented launcher) when it is executable, and
    the current interpreter plus ``main.py --gui`` everywhere else. On Windows
    that interpreter is ``pythonw.exe`` when available so logging in does not
    open a console window.
    """
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if (_platform() == "darwin" and len(executable.parents) >= 3
                and executable.parents[2].suffix == ".app"):
            # Launch the .app through LaunchServices so macOS gives it the
            # normal activation, dock icon and privacy prompts. Running the
            # inner Mach-O binary directly from a LaunchAgent is unreliable.
            return ["/usr/bin/open", str(executable.parents[2])]
        return [sys.executable]
    root = _root()
    run_sh = root / "run.sh"
    if (use_run_sh and not _platform().startswith("win")
            and run_sh.exists() and os.access(run_sh, os.X_OK)):
        return [str(run_sh)]

    executable = sys.executable
    if _platform().startswith("win"):
        # python.exe would show a console window at login; pythonw.exe is the
        # same interpreter without one (it normally sits next to it in venv).
        pythonw = Path(executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    return [executable, str(root / "main.py"), "--gui"]


# --- Linux ------------------------------------------------------------------ #
def _desktop_quote(value):
    """Quote one ``Exec=`` argument for the Desktop Entry specification."""
    text = str(value).replace("%", "%%")
    if any(ch in text for ch in ' \t"\'\\<>~|&;$*?#`'):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _desktop_entry():
    arguments = _launch_arguments(use_run_sh=True)
    exec_line = " ".join(_desktop_quote(arg) for arg in arguments)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={APP_NAME}\n"
        "GenericName=Voice Assistant\n"
        "Comment=Start JARVIS at login\n"
        f"Exec={exec_line}\n"
        f"Path={_root()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _linux_set(enable, home=None):
    path = linux_autostart_path(home)
    if not enable:
        if path.exists():
            path.unlink()
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_desktop_entry(), encoding="utf-8")
    return path


# --- macOS ------------------------------------------------------------------ #
def _macos_set(enable, home=None):
    path = macos_plist_path(home)
    if not enable:
        if path.exists():
            path.unlink()
        return None
    payload = {
        "Label": APP_ID,
        "ProgramArguments": _launch_arguments(use_run_sh=False),
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)
    return path


# --- Windows ---------------------------------------------------------------- #
def _load_winreg():
    """Import ``winreg`` lazily so this module imports on Linux/macOS too."""
    import winreg
    return winreg


def _windows_value():
    """Return the current Run value, or ``None`` when the key/value is absent."""
    winreg = _load_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, _VALUE_NAME)
            return value
    except OSError:
        return None


def _windows_set(enable):
    winreg = _load_winreg()
    if not enable:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _VALUE_NAME)
        except FileNotFoundError:
            pass
        return None

    command = subprocess.list2cmdline(_launch_arguments(use_run_sh=False))
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
    return f"{_RUN_KEY}\\{_VALUE_NAME}"


# --- public API ------------------------------------------------------------- #
def set_autostart(enable, home=None):
    """Enable or disable starting JARVIS at login.

    Returns the path (or registry location) written when enabling, and
    ``None`` when disabling. Idempotent: calling it twice is safe.

    Raises ``OSError`` if the platform refuses the write (for example a
    read-only home directory or a locked registry key).
    """
    enable = bool(enable)
    platform = _platform()
    if platform.startswith("win"):
        return _windows_set(enable)
    if platform == "darwin":
        return _macos_set(enable, home=home)
    return _linux_set(enable, home=home)


def is_autostart_enabled(home=None):
    """True when the platform's login entry currently exists."""
    platform = _platform()
    if platform.startswith("win"):
        return _windows_value() is not None
    if platform == "darwin":
        return macos_plist_path(home).exists()
    return linux_autostart_path(home).exists()


def status_text(home=None):
    """Human-readable status used by the CLI and the doctor report."""
    return "Enabled" if is_autostart_enabled(home=home) else "Disabled"
