"""Single-instance guard and wake signalling for the Jarvis GUI.

Jarvis is a background tray application, so starting it twice would give you two
tray icons, two global hotkeys and two processes competing for the microphone.
A lock file at ``~/.jarvis/jarvis.lock`` guarantees that only one Jarvis runs; a
tiny Unix socket at ``~/.jarvis/jarvis.sock`` lets a second launch (or
``main.py --wake``) ask the running instance to wake up and then exit.

Nothing here raises on a normal path: on a platform without ``fcntl`` or Unix
sockets the guard simply degrades to "do not enforce" / "cannot signal".
"""

import os
import socket
import threading
from pathlib import Path

try:                                             # POSIX only
    import fcntl
except ImportError:                              # pragma: no cover - Windows
    fcntl = None

_WAKE_MESSAGE = b"wake\n"


class AlreadyRunning(Exception):
    """Raised by :func:`acquire` when another Jarvis already holds the lock."""


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


def acquire():
    """Take the single-instance lock; raise :class:`AlreadyRunning` when held.

    Returns an opaque handle that must be handed back to :func:`release`. The
    handle keeps the OS lock open for the lifetime of the process, so a crash
    automatically frees it (the file itself may stay behind).
    """
    path = lock_path()
    if fcntl is None:                            # pragma: no cover - Windows
        if path.exists():
            raise AlreadyRunning("jarvis.lock already exists")
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
    else:
        handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(handle)
            raise AlreadyRunning("jarvis.lock is held by another process")
    try:
        os.ftruncate(handle, 0)
        os.write(handle, str(os.getpid()).encode("ascii"))
    except OSError:
        pass
    return handle


def release(handle):
    """Release a handle from :func:`acquire`; safe to call more than once."""
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(handle)
    except OSError:
        pass


def signal_wake(path=None, timeout=1.0):
    """Ask the running instance to wake up. True when the message was sent."""
    if not hasattr(socket, "AF_UNIX"):           # pragma: no cover - Windows
        return False
    target = path or socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(target)
            sock.sendall(_WAKE_MESSAGE)
        return True
    except OSError:
        return False


class WakeServer:
    """Listen on the wake socket and run ``callback`` for each "wake" message.

    The listener runs on a daemon thread. The callback must be thread-safe; the
    GUI passes a Qt signal's ``emit``, which Qt delivers on the GUI thread.
    """

    def __init__(self, callback, path=None):
        self._callback = callback
        self._path = path or socket_path()
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        """Bind and start listening. False when the socket is unavailable."""
        if not hasattr(socket, "AF_UNIX"):       # pragma: no cover - Windows
            return False
        try:
            if os.path.exists(self._path):
                os.unlink(self._path)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(self._path)
            self._sock.listen(4)
            self._sock.settimeout(0.5)
        except OSError:
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
        try:
            os.unlink(self._path)
        except OSError:
            pass
