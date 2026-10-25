"""Categorized, rotating error logs for Jarvis.

Two independent file loggers keep unrelated failures apart so debugging is easy:

* :data:`api_logger` writes API / network failures (Groq, vision, web fetches,
  speech-to-text requests) to ``~/.jarvis/logs/api_errors.log``.
* :data:`command_logger` writes tool / desktop / audio / routing failures to
  ``~/.jarvis/logs/command_errors.log``.

Both rotate at 5 MB and never write to the terminal: the console stays reserved
for normal INFO output such as "Listening..." and "Thinking...". A full
traceback is stored in the file, while the user only ever sees a short friendly
sentence.

Set ``JARVIS_LOG_DIR`` to move the log directory (the test suite uses this so a
test run never touches the developer's real ``~/.jarvis/logs``).
"""

import logging
import os
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024          # 5 MB per file
BACKUP_COUNT = 3                     # keep three rotated copies
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

API_LOG = "api_errors.log"
COMMAND_LOG = "command_errors.log"

# Marker set on an exception after it has been logged once. ``main.handle`` and
# ``agent.run_agent`` both catch errors on the way out, so the marker prevents
# the same traceback from being written twice.
_LOGGED_FLAG = "_jarvis_logged"


def log_dir():
    """Return the directory that holds the error logs.

    Resolved on every call (not at import) so ``HOME`` and ``JARVIS_LOG_DIR``
    overrides, including the ones the tests set, always take effect.
    """
    override = (os.getenv("JARVIS_LOG_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis" / "logs"


def _make_logger(name):
    logger = logging.getLogger(f"jarvis.{name}")
    logger.setLevel(logging.ERROR)
    # Keep these records out of the root logger (and therefore the terminal).
    logger.propagate = False
    return logger


api_logger = _make_logger("api")
command_logger = _make_logger("command")

# Serialise handler setup: two worker threads (agent + voice) can hit their
# first error at the same moment.
_handler_lock = threading.Lock()


def _ensure_handler(logger, filename):
    """Attach one rotating handler for ``filename`` to ``logger``.

    The handler is recreated when the log directory moves (a test or a user
    changing ``JARVIS_LOG_DIR``), and setup failures are swallowed so logging can
    never stop Jarvis from running.
    """
    with _handler_lock:
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            target = os.path.abspath(str(directory / filename))

            for handler in list(logger.handlers):
                if getattr(handler, "baseFilename", None) == target:
                    return logger
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass

            handler = RotatingFileHandler(
                target,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            logger.addHandler(handler)
            # A disk-full or permissions problem must not print a logging
            # traceback to the console; the app is still allowed to continue.
            logging.raiseExceptions = False
        except Exception:
            # A read-only home directory or an exhausted disk must not break the
            # app.
            pass
    return logger


def _mark_logged(exc):
    try:
        setattr(exc, _LOGGED_FLAG, True)
    except Exception:
        pass


def was_logged(exc):
    """True when ``exc`` has already been written to one of the log files."""
    return bool(getattr(exc, _LOGGED_FLAG, False))


def _write(logger, filename, context, exc=None):
    """Write one record, including a full traceback when ``exc`` is supplied."""
    try:
        _ensure_handler(logger, filename)
        message = str(context or "Unexpected error")
        if exc is not None:
            message = f"{message}: {type(exc).__name__}: {exc}"
            logger.error(
                message,
                exc_info=(type(exc), exc, getattr(exc, "__traceback__", None)),
            )
            _mark_logged(exc)
        else:
            logger.error(message)
    except Exception:
        # Logging is best-effort and must never raise into a tool or the loop.
        pass


def log_api_error(context, exc=None):
    """Log an API/network failure (with traceback) to ``api_errors.log``."""
    _write(api_logger, API_LOG, context, exc)


def log_command_error(context, exc=None):
    """Log a tool/command/audio failure with traceback to ``command_errors.log``."""
    _write(command_logger, COMMAND_LOG, context, exc)


def tail(filename, lines=20):
    """Return the last ``lines`` lines of a log file, or a short placeholder."""
    path = log_dir() / filename
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=max(1, int(lines)))).rstrip()
    except FileNotFoundError:
        return "(no entries yet)"
    except OSError:
        return f"(could not read {filename})"


def format_logs(lines=20):
    """Render both logs for ``main.py --logs``."""
    sections = []
    for label, filename in (
        ("API errors", API_LOG),
        ("Command errors", COMMAND_LOG),
    ):
        sections.append(f"=== {label}: {log_dir() / filename} ===")
        sections.append(tail(filename, lines) or "(no entries yet)")
        sections.append("")
    return "\n".join(sections).rstrip()


def configure():
    """Create ``~/.jarvis/logs`` (or ``JARVIS_LOG_DIR``) if it is missing.

    Log files themselves are opened lazily on the first error, so a clean run
    leaves no empty clutter behind. A read-only home must never break startup.
    """
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir()


# Create the directory as soon as the module is imported, as the setup step
# promises. Failure is deliberately silent; logging remains best-effort.
configure()


def command_guard(func):
    """Decorator that logs tool failures and returns a friendly sentence.

    Used by :mod:`tools` and :mod:`desktop` around user-facing actions. The
    original signature and docstring are preserved, so the agent can still build
    its OpenAI tool schema from the wrapped function.
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            log_command_error(f"{func.__name__} failed", exc)
            return "I couldn't execute that command, sir."

    return wrapper
