"""Single-instance guard and wake signalling for the Jarvis GUI.

Jarvis is a background tray application, so starting it twice would give you two
tray icons, two global hotkeys and two processes competing for the microphone.
A lock file at ``~/.jarvis/jarvis.lock`` guarantees that only one Jarvis runs; a
tiny Unix socket at ``~/.jarvis/jarvis.sock`` lets a second launch (or
``main.py --wake`` / ``--quit``) ask the running instance to wake up or exit.

Nothing here raises on a normal path: on a platform without a lock primitive the
guard simply degrades to "do not enforce", and without Unix sockets or a live
PID the signal falls back to "cannot signal".
"""

import ctypes
import os
import signal
import socket
import threading
from pathlib import Path

try:                                             # POSIX only
    import fcntl
except ImportError:                              # pragma: no cover - Windows
    fcntl = None

_WAKE_MESSAGE = b"wake\n"
_QUIT_MESSAGE = b"quit\n"

#: Windows single-instance mutex. "Local\\" scopes it to the logon session, and
#: the kernel releases it the moment the owning process exits -- which is what a
#: byte-range lock on the lock file was needed for (and which the old
#: "does jarvis.lock exist?" check did not provide at all).
_MUTEX_NAME = "Local\\jarvis-ai-voice-assistant-single-instance"
_ERROR_ALREADY_EXISTS = 183

if os.name == "nt":                              # pragma: no cover - Windows
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                       wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:                                            # pragma: no cover - POSIX
    _kernel32 = None


class AlreadyRunning(Exception):
    """Raised by :func:`acquire` when another Jarvis already holds the lock."""


class _WindowsLock:
    """Owns a named Windows mutex for as long as the process lives."""

    def __init__(self, handle):
        self._handle = handle

    def close(self):
        handle, self._handle = self._handle, None
        if handle:
            _kernel32.CloseHandle(handle)


def state_dir():
    """Directory holding the lock + socket (``~/.jarvis``, or an env override)."""
    override = os.environ.get("JARVIS_STATE_DIR")
    base = Path(override) if override else Path.home() / ".jarvis"
    base.mkdir(parents=True, exist_ok=True)
    return base


def lock_path():
    """Path of the single-instance lock file."""
    return state_dir() / "jarvis.lock"


def socket_path():
    """Path of the wake socket."""
    return str(state_dir() / "jarvis.sock")


def port_path():
    """Path of the loopback-port file used where ``AF_UNIX`` is unavailable.

    Windows builds of Python ship without ``AF_UNIX``, so wake/quit signalling
    there goes over ``127.0.0.1`` and the port is published in this file.
    """
    return str(state_dir() / "jarvis.port")


def _has_unix_socket():
    """True when this platform can bind a Unix socket (not Windows here)."""
    return hasattr(socket, "AF_UNIX")


def _take_lock():
    """Take the exclusive lock, or raise :class:`AlreadyRunning`.

    POSIX uses ``flock`` on the lock file. Windows uses a named mutex because a
    byte-range lock on the file also blocks other processes from *reading* it
    (which broke ``main.py --quit`` and the PID lookup). Both are released by
    the OS when the process dies, so a crash or a Task Manager kill can never
    leave a stale lock that refuses every later launch.
    """
    path = lock_path()

    if fcntl is not None:
        handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(handle)
            raise AlreadyRunning("jarvis.lock is held by another process")
        return handle

    if _kernel32 is not None:                    # pragma: no cover - Windows
        handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if not handle:
            raise AlreadyRunning("the Jarvis instance mutex is unavailable")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            raise AlreadyRunning("another Jarvis already owns the instance mutex")
        return _WindowsLock(handle)

    # No locking primitive available: fall back to a plain existence check.
    if path.exists():                            # pragma: no cover - exotic OS
        raise AlreadyRunning("jarvis.lock already exists")
    return None


def acquire():
    """Take the single-instance lock; raise :class:`AlreadyRunning` when held.

    Returns an opaque handle that must be handed back to :func:`release`. The
    handle keeps the OS lock open for the lifetime of the process, so a crash
    automatically frees it (the file itself may stay behind and is harmless).
    """
    handle = _take_lock()
    try:
        lock_path().write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    return handle


def release(handle):
    """Release a handle from :func:`acquire`; safe to call more than once."""
    if handle is None:
        return
    close = getattr(handle, "close", None)        # Windows mutex wrapper
    if callable(close):
        close()
        return
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        os.close(handle)
    except OSError:
        pass


def running_pid():
    """Return the PID recorded in the lock file, or ``None`` when unreadable."""
    try:
        text = lock_path().read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _listener_address(path=None):
    """Return ``(family, address)`` for the running instance, or ``None``.

    A Unix socket is preferred. Where the platform has none, the published
    loopback port is used instead.
    """
    if _has_unix_socket():
        target = path or socket_path()
        if os.path.exists(target):
            return socket.AF_UNIX, target
        return None
    try:
        raw = Path(port_path()).read_text(encoding="ascii").strip()
        port = int(raw)
    except (OSError, ValueError):
        return None
    return socket.AF_INET, ("127.0.0.1", port)


def _send(message, path=None, timeout=1.0):
    """Deliver one control message to the running instance's socket."""
    target = _listener_address(path)
    if target is None:
        return False
    family, address = target
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(address)
            sock.sendall(message)
        return True
    except OSError:
        return False


def signal_wake(path=None, timeout=1.0):
    """Ask the running instance to wake up. True when the message was sent."""
    return _send(_WAKE_MESSAGE, path, timeout)


def _is_jarvis_pid(pid):
    """True only when ``pid`` is a Python process running this checkout's main.py.

    When a Jarvis crashes, its PID file stays behind, and both Windows and Linux
    reuse PIDs. Terminating that number blindly could kill an unrelated program.
    This check makes the Windows fallback (there is no ``AF_UNIX`` there) safe:
    the process must be Python *and* its command line or working directory must
    point at this project.
    """
    try:
        import psutil

        proc = psutil.Process(pid)
        if "python" not in (proc.name() or "").lower():
            return False
        command = " ".join(proc.cmdline() or [])
        if "main.py" not in command.lower():
            return False
        root = os.path.normcase(str(Path(__file__).resolve().parent)).lower()
        if root in os.path.normcase(command).lower():
            return True
        try:
            return os.path.normcase(proc.cwd() or "").lower() == root
        except (psutil.Error, OSError):
            return False
    except Exception:
        return False


def signal_quit(path=None, timeout=1.0):
    """Ask the running instance to exit. True when the request was delivered.

    A Unix socket is used where the platform has one. Windows builds without
    ``AF_UNIX`` fall back to terminating the PID recorded in the lock file, but
    only after :func:`_is_jarvis_pid` confirms it is this project's Jarvis (a
    stale PID must never kill an unrelated process).
    """
    if _send(_QUIT_MESSAGE, path, timeout):
        return True
    if os.name == "nt":                          # pragma: no cover - Windows
        pid = running_pid()
        if pid and pid != os.getpid() and _is_jarvis_pid(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except OSError:
                return False
    return False


class WakeServer:
    """Listen on the wake socket and run ``callback`` for each "wake" message.

    The listener runs on a daemon thread. The callback must be thread-safe; the
    GUI passes a Qt signal's ``emit``, which Qt delivers on the GUI thread.
    ``on_quit`` (optional) is called for a "quit" message, which is how
    ``main.py --quit`` closes a hidden-to-tray Jarvis.
    """

    def __init__(self, callback, path=None, on_quit=None):
        self._callback = callback
        self._on_quit = on_quit
        self._path = path or socket_path()
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        """Bind and start listening. False when no listener can be created."""
        try:
            if _has_unix_socket():
                if os.path.exists(self._path):
                    os.unlink(self._path)
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.bind(self._path)
            else:                                # pragma: no cover - Windows
                # No AF_UNIX: listen on a random loopback port and publish it.
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._sock.bind(("127.0.0.1", 0))
                Path(port_path()).write_text(
                    str(self._sock.getsockname()[1]), encoding="ascii")
            self._sock.listen(4)
            self._sock.settimeout(0.5)
        except OSError:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None
            return False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="jarvis-wake")
        self._thread.start()
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64).decode("utf-8", "replace").strip()
            except OSError:
                data = ""
            finally:
                conn.close()
            if data == "wake":
                try:
                    self._callback()
                except Exception:
                    pass
            elif data == "quit" and self._on_quit is not None:
                try:
                    self._on_quit()
                except Exception:
                    pass

    def stop(self):
        """Stop the thread and remove the socket file (idempotent)."""
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        for path in (self._path, port_path()):
            try:
                os.unlink(path)
            except OSError:
                pass
