"""Categorized error logging: API failures vs command/tool failures."""

import pytest

import logger
import main
import tools
import vision


@pytest.fixture(autouse=True)
def _log_dir(tmp_path, monkeypatch):
    """Point both loggers at a fresh directory for every test."""
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    return tmp_path


def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_api_error_is_written_to_api_log(_log_dir, monkeypatch, tmp_path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake")

    class BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("model exploded")

    monkeypatch.setattr(vision, "_client", BrokenClient)

    reply = vision.ask_image(image, "what is on screen?")

    # The user only sees a friendly sentence, never provider internals.
    assert "cannot access my vision" in reply.lower()
    assert "model exploded" not in reply

    api_log = _read(_log_dir / "api_errors.log")
    assert "Vision API request failed" in api_log
    assert "RuntimeError: model exploded" in api_log
    assert "Traceback (most recent call last)" in api_log
    # A vision/network failure must not be filed as a command error.
    assert not (_log_dir / "command_errors.log").exists()


def test_tool_error_is_written_to_command_log(_log_dir, monkeypatch):
    def broken_open(_name):
        raise FileNotFoundError("xdotool is missing")

    monkeypatch.setattr(tools.desktop, "open_app", broken_open)

    reply = tools.fn_open_app("calculator")

    assert reply == "I couldn't execute that command, sir."

    command_log = _read(_log_dir / "command_errors.log")
    assert "fn_open_app failed" in command_log
    assert "FileNotFoundError: xdotool is missing" in command_log
    assert "Traceback (most recent call last)" in command_log
    # A tool failure must not be filed as an API error.
    assert not (_log_dir / "api_errors.log").exists()


def test_direct_loggers_rotate_at_five_megabytes(_log_dir):
    logger.log_api_error("api context", RuntimeError("api boom"))
    logger.log_command_error("command context", RuntimeError("command boom"))

    for name in ("api_errors.log", "command_errors.log"):
        path = _log_dir / name
        assert path.exists()
        handler = next(
            h for h in (logger.api_logger.handlers + logger.command_logger.handlers)
            if getattr(h, "baseFilename", "") == str(path)
        )
        assert handler.maxBytes == 5 * 1024 * 1024
        assert handler.backupCount == 3


def test_logs_flag_prints_both_tails(_log_dir, monkeypatch, capsys):
    logger.log_api_error("api context", RuntimeError("api boom"))
    logger.log_command_error("command context", RuntimeError("command boom"))
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--logs"])

    assert main.main() == 0

    out = capsys.readouterr().out
    assert "API errors" in out
    assert "Command errors" in out
    assert "api boom" in out
    assert "command boom" in out
