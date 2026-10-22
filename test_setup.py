"""First-run setup tests: key detection, secure storage, CLI and GUI flows.

The real ``~/.jarvis/.env`` is never touched: every storage test writes to
``tmp_path`` or replaces ``config.save_api_key`` with a recorder.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import config
import main


# --- key detection ---------------------------------------------------------- #
def test_missing_and_placeholder_keys_are_not_configured():
    for value in ("", "   ", None, "your_key_here", "YOUR_KEY_HERE",
                  "your-key-here", "gsk_your_key_here", "changeme"):
        assert not config.is_api_key_configured(value), value


def test_real_key_is_recognised():
    assert config.is_api_key_configured("gsk_abc123")


# --- secure storage --------------------------------------------------------- #
def test_save_api_key_writes_a_private_file_and_preserves_settings(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "apply_api_key", lambda key: key)
    env = tmp_path / ".jarvis" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("USE_LOCAL_LLM=False\nGROQ_API_KEY=gsk_old\n", encoding="utf-8")

    written = config.save_api_key("gsk_new_value", path=env)

    assert written == env
    text = env.read_text(encoding="utf-8")
    assert "GROQ_API_KEY=gsk_new_value" in text
    assert "GROQ_API_KEY=gsk_old" not in text
    assert text.count("GROQ_API_KEY=") == 1
    assert "USE_LOCAL_LLM=False" in text, "unrelated settings must survive"
    if os.name != "nt":
        # Windows has no POSIX permission bits; the file inherits the
        # user-profile ACL, so only assert the mode where it is meaningful.
        assert (env.stat().st_mode & 0o777) == 0o600
        assert (env.parent.stat().st_mode & 0o777) == 0o700


def test_save_api_key_creates_the_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "apply_api_key", lambda key: key)
    env = tmp_path / "fresh" / ".jarvis" / ".env"

    config.save_api_key("gsk_created", path=env)

    assert env.exists()
    assert "GROQ_API_KEY=gsk_created" in env.read_text(encoding="utf-8")
    if os.name != "nt":
        # See the note above: mode bits are a POSIX concept.
        assert (env.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("bad", ["", "your_key_here", "   "])
def test_save_api_key_rejects_placeholders(tmp_path, bad):
    with pytest.raises(ValueError):
        config.save_api_key(bad, path=tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_apply_api_key_updates_the_process_and_refreshes_the_agent(monkeypatch):
    refreshed = []
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setattr(config, "API_KEY", None, raising=False)
    monkeypatch.setattr("agent.refresh_client", lambda: refreshed.append(True))

    config.apply_api_key("gsk_live_key")

    assert config.API_KEY == "gsk_live_key"
    assert os.environ["GROQ_API_KEY"] == "gsk_live_key"
    assert refreshed == [True]


# --- CLI flow --------------------------------------------------------------- #
def test_cli_setup_prints_welcome_and_saves_the_key(tmp_path, monkeypatch,
                                                    capsys):
    saved = []
    env = tmp_path / ".jarvis" / ".env"
    monkeypatch.setattr(
        config, "save_api_key",
        lambda key: saved.append(key) or env,
    )

    key = main.prompt_for_api_key(
        input_fn=lambda _prompt: "gsk_pasted_key",
        print_fn=print,
    )

    assert key == "gsk_pasted_key"
    assert saved == ["gsk_pasted_key"]
    out = capsys.readouterr().out
    assert main.SETUP_WELCOME in out
    assert "https://console.groq.com/keys" in out
    assert f"API key saved to {env}" in out


def test_cli_setup_blank_input_switches_to_offline(monkeypatch):
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: False)
    monkeypatch.setattr(main, "_OFFLINE", False)

    assert main.ensure_api_key(input_fn=lambda _p: "", print_fn=lambda *a: None) is False

    assert main._OFFLINE is True


def test_cli_setup_eof_switches_to_offline(monkeypatch):
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: False)
    monkeypatch.setattr(main, "_OFFLINE", False)

    def eof(_prompt):
        raise EOFError

    result = main.ensure_api_key(input_fn=eof, print_fn=lambda *a: None)

    assert result is False
    assert main._OFFLINE is True


def test_cli_setup_is_skipped_when_a_key_exists(monkeypatch):
    called = []
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: True)
    monkeypatch.setattr(main, "prompt_for_api_key", lambda **kw: called.append(True))

    assert main.ensure_api_key() is True
    assert called == []


def test_cli_main_runs_setup_before_the_command_loop(monkeypatch):
    calls = []
    import instance

    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: False)
    monkeypatch.setattr(main, "ensure_api_key", lambda: calls.append("setup"))
    monkeypatch.setattr(main, "_run_loop", lambda: calls.append("loop"))
    monkeypatch.setattr(instance, "acquire", lambda: object())
    monkeypatch.setattr(instance, "release", lambda lock: None)

    main.main()

    assert calls == ["setup", "loop"]


# --- GUI flow --------------------------------------------------------------- #
pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLineEdit  # noqa: E402

import gui  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_dialog_shows_the_welcome_copy_and_masks_the_key(app):
    dialog = gui.ApiKeyDialog()
    try:
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Welcome to JARVIS. Please enter your Groq API Key:" in labels
        assert dialog.key_input.echoMode() == QLineEdit.Password
        assert dialog.save_button.text() == "Save & Start"
        assert dialog.console_button.text() == "Get a free key"
        assert dialog.console_button.toolTip() == "https://console.groq.com/keys"
    finally:
        dialog.close()


def test_dialog_saves_the_key_and_accepts(app, monkeypatch, tmp_path):
    saved = []
    target = tmp_path / ".jarvis" / ".env"
    monkeypatch.setattr(
        config, "save_api_key",
        lambda key: saved.append(key) or target,
    )

    dialog = gui.ApiKeyDialog()
    dialog.key_input.setText("gsk_from_dialog")
    dialog._save()

    assert saved == ["gsk_from_dialog"]
    assert dialog.saved_path == target
    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_dialog_rejects_an_empty_key_without_closing(app):
    dialog = gui.ApiKeyDialog()
    dialog.key_input.setText("")
    dialog._save()

    assert dialog.result() != QDialog.Accepted
    assert "valid Groq API key" in dialog.error_label.text()
    dialog.close()


def test_run_setup_wizard_returns_true_when_a_key_exists(app, monkeypatch):
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: True)

    assert gui.run_setup_wizard() is True


def test_run_setup_wizard_saves_then_returns_true(app, monkeypatch):
    class FakeDialog:
        def exec_(self):
            return QDialog.Accepted

        def api_key(self):
            return "gsk_fake"

    calls = {"n": 0}

    def configured(key=None):
        # First check: missing. Second check after the (fake) save: present.
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(gui, "ApiKeyDialog", FakeDialog)
    monkeypatch.setattr(config, "is_api_key_configured", configured)

    assert gui.run_setup_wizard() is True


def test_run_setup_wizard_dismissed_enables_offline(app, monkeypatch):
    class FakeDialog:
        def exec_(self):
            return QDialog.Rejected

        def api_key(self):
            return ""

    monkeypatch.setattr(gui, "ApiKeyDialog", FakeDialog)
    monkeypatch.setattr(config, "is_api_key_configured", lambda key=None: False)
    monkeypatch.setattr(main, "_OFFLINE", False)

    assert gui.run_setup_wizard() is False

    assert main._OFFLINE is True
