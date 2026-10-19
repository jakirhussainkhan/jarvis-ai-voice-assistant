"""Small, OS-neutral process helpers shared by every platform adapter.

These wrap :mod:`subprocess` and :mod:`shutil` so the concrete adapters never
have to talk to them directly (and so tests have a single, stable patch seam):
``run`` executes a command and returns ``(returncode, output)``, ``has`` reports
whether a program is on ``PATH``, and ``spawn`` starts a detached program.

Nothing here is Linux-specific: the *commands* live in the per-OS adapters.
"""

import os
import re
import shutil
import subprocess

import psutil

DEVNULL = subprocess.DEVNULL

# System / session processes that must never be closed or killed: the display
# server, the session manager, systemd/dbus and the desktop shell. psutil and
# this list are cross-platform, so they live in the shared layer.
PROTECTED_NAMES = {
    "systemd", "systemd-journal", "systemd-journald", "systemd-logind",
    "systemd-udevd", "init", "dbus-daemon", "dbus-broker",
    "xorg", "xwayland", "x", "xinit",
    "cinnamon", "cinnamon-session", "cinnamon-screensaver", "cinnamon-launcher",
    "gnome-shell", "gnome-session", "gnome-session-binary",
    "plasmashell", "plasma_session", "ksmserver", "kwin_x11", "kwin_wayland",
    "mutter", "xfwm4", "xfce4-session", "marco", "metacity",
    "lightdm", "gdm", "gdm3", "sddm", "lxdm",
    "pipewire", "pipewire-pulse", "wireplumber", "pulseaudio",
    "explorer", "winlogon", "services", "lsass", "csrss", "smss", "dwm",
    "finder", "dock", "windowserver", "loginwindow",
}
_PROTECTED_RE = re.compile(
    r"(jarvis|python|systemd|dbus|xorg|xwayland|cinnamon|gnome-shell|"
    r"gnome-session|plasmashell|kwin|mutter|marco|xfwm|xfce4-session|"
    r"lightdm|gdm|sddm|pipewire|pulseaudio|wireplumber|session|shell|"
    r"explorer|winlogon|windowserver|launchd)",
    re.I,
)


def protected_pids():
    """PIDs of Jarvis itself and every ancestor (the terminal/shell chain)."""
    pids = {1}
    try:
        proc = psutil.Process(os.getpid())
        for ancestor in proc.parents():
            pids.add(ancestor.pid)
    except psutil.Error:
        pass
    pids.add(os.getpid())
    return pids


def is_protected(name, pid=None):
    """True when a process must never be closed or killed."""
    if pid is not None and pid in protected_pids():
        return True
    if not name:
        return False
    base = name.lower().split("/")[-1].split("\\")[-1]
    if base in PROTECTED_NAMES:
        return True
    return bool(_PROTECTED_RE.search(base))


def app_tokens(app):
    """Identifying process-name tokens for an application object."""
    tokens = set()
    if getattr(app, "exec_bin", ""):
        tokens.add(app.exec_bin.lower())
    raw_name = re.sub(r"[^a-z0-9]+", " ", (getattr(app, "name", "") or "").lower())
    for word in raw_name.split():
        if len(word) >= 3:
            tokens.add(word)
    desktop_id = getattr(app, "desktop_id", "") or ""
    if desktop_id:
        tokens.add(desktop_id.split(".")[-1].lower())
    return tokens


def matching_pids(app):
    """PIDs whose executable name matches the app's binary/name tokens."""
    tokens = app_tokens(app)
    protected = protected_pids()
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
        except psutil.Error:
            continue
        pid = info.get("pid")
        pname = (info.get("name") or "").lower()
        if pid in protected or not pname:
            continue
        if pname in tokens or any(
            t and (pname == t or pname.startswith(t + ".")) for t in tokens
        ):
            pids.append(pid)
    return pids


def terminate(pids, timeout=3.0):
    """SIGTERM the PIDs, wait, then SIGKILL survivors. Returns count signalled."""
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.Error:
            continue
    if not procs:
        return 0
    for proc in procs:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    return len(procs)


def matching_process_pids(name, limit=200):
    """PIDs of non-protected processes whose name contains ``name``."""
    query = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not query:
        return []
    protected = protected_pids()
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
        except psutil.Error:
            continue
        pid = info.get("pid")
        pname = info.get("name") or ""
        if pid in protected or not pname or is_protected(pname, pid):
            continue
        if query in re.sub(r"[^a-z0-9]+", " ", pname.lower()):
            pids.append(pid)
            if len(pids) >= limit:
                break
    return pids


def is_wayland():
    """True when the session is Wayland (X11 window tools do not work there)."""
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def has(tool):
    """True if an executable named ``tool`` is on ``PATH``."""
    return shutil.which(tool) is not None


def run(args, timeout=6):
    """Run a command, never raising. Returns ``(returncode, output)``.

    ``returncode`` is 127 when the program is missing. Output is stripped text
    with stderr folded in, so a caller can surface the tool's own error.
    """
    if not args:
        return 127, ""
    if shutil.which(args[0]) is None:
        return 127, f"{args[0]} is not installed"
    try:
        proc = subprocess.run(
            args,
            stdin=DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def spawn(args):
    """Start a detached program with its output discarded. True on success."""
    try:
        subprocess.Popen(
            args,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def open_url(url):
    """Open a URL in the default browser. True on success."""
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
