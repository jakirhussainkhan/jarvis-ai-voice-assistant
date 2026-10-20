"""Tests for cross-platform auto-start (startup.py) and its CLI/doctor wiring."""

import plistlib
import sys

import pytest

import doctor
import main
import startup


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(startup, "_platform", lambda: "linux")


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(startup, "_platform", lambda: "darwin")


# --- Linux ------------------------------------------------------------------ #
def test_linux_enable_creates_the_xdg_autostart_entry(linux, tmp_path):
    path = startup.linux_autostart_path(tmp_path)

    assert startup.is_autostart_enabled(home=tmp_path) is False
    written = startup.set_autostart(True, home=tmp_path)

    assert written == path
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Type=Application" in text
    assert "Name=JARVIS" in text
    assert "run.sh" in text, "the absolute launcher path must be used"
    assert "Terminal=false" in text
    assert startup.is_autostart_enabled(home=tmp_path) is True
    assert startup.status_text(home=tmp_path) == "Enabled"


def test_linux_disable_removes_the_entry(linux, tmp_path):
    startup.set_autostart(True, home=tmp_path)

    assert startup.set_autostart(False, home=tmp_path) is None
    assert not startup.linux_autostart_path(tmp_path).exists()
    assert startup.is_autostart_enabled(home=tmp_path) is False
    assert startup.status_text(home=tmp_path) == "Disabled"


def test_linux_enable_is_idempotent(linux, tmp_path):
    startup.set_autostart(True, home=tmp_path)
    startup.set_autostart(True, home=tmp_path)

    text = startup.linux_autostart_path(tmp_path).read_text(encoding="utf-8")
    assert text.count("Name=JARVIS") == 1


def test_linux_falls_back_to_python_when_run_sh_is_missing(linux, tmp_path,
                                                          monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "main.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(startup, "_root", lambda: root)

    startup.set_autostart(True, home=tmp_path)

    text = startup.linux_autostart_path(tmp_path).read_text(encoding="utf-8")
    assert "main.py" in text
    assert "--gui" in text


# --- macOS ------------------------------------------------------------------ #
def test_macos_enable_writes_a_launch_agent(macos, tmp_path):
    path = startup.macos_plist_path(tmp_path)

    written = startup.set_autostart(True, home=tmp_path)

    assert written == path
    payload = plistlib.loads(path.read_bytes())
    assert payload["Label"] == "com.jarvis.assistant"
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is False
    assert any("main.py" in str(arg) for arg in payload["ProgramArguments"])
    assert "--gui" in payload["ProgramArguments"]
    assert startup.is_autostart_enabled(home=tmp_path) is True


def test_macos_disable_removes_the_launch_agent(macos, tmp_path):
    path = startup.macos_plist_path(tmp_path)
    startup.set_autostart(True, home=tmp_path)

    assert startup.set_autostart(False, home=tmp_path) is None
    assert not path.exists()
    assert startup.is_autostart_enabled(home=tmp_path) is False


# --- Windows ---------------------------------------------------------------- #
class _FakeKey:
    def __init__(self):
        self.values = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.key = _FakeKey()

    def OpenKey(self, hive, path, reserved, access):
        assert hive == "HKCU"
        assert path == startup._RUN_KEY
        return self.key

    def CreateKeyEx(self, hive, path, reserved, access):
        assert hive == "HKCU"
        assert path == startup._RUN_KEY
        return self.key

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


@pytest.fixture
def windows(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setattr(startup, "_platform", lambda: "win32")
    monkeypatch.setattr(startup, "_load_winreg", lambda: fake)
    return fake


def test_windows_enable_writes_the_run_key(windows):
    assert startup.is_autostart_enabled() is False

    location = startup.set_autostart(True)

    assert location == rf"{startup._RUN_KEY}\{startup._VALUE_NAME}"
    command = windows.key.values["JARVIS"]
    assert sys.executable in command
    assert "main.py" in command
    assert "--gui" in command
    assert startup.is_autostart_enabled() is True
    assert startup.status_text() == "Enabled"


def test_windows_disable_deletes_the_run_value(windows):
    startup.set_autostart(True)

    assert startup.set_autostart(False) is None
    assert startup.is_autostart_enabled() is False


def test_windows_disable_is_safe_when_missing(windows):
    assert startup.set_autostart(False) is None
    assert startup.is_autostart_enabled() is False


# --- CLI -------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,expected", [
    ("--autostart-enable", True),
    ("--autostart-disable", False),
])
def test_cli_flags_toggle_autostart(monkeypatch, capsys, flag, expected):
    calls = []
    monkeypatch.setattr(startup, "set_autostart", lambda enable: calls.append(enable))
    monkeypatch.setattr(startup, "status_text",
                        lambda: "Enabled" if expected else "Disabled")
    monkeypatch.setattr(main.sys, "argv", ["main.py", flag])

    assert main.main() == 0

    assert calls == [expected]
    out = capsys.readouterr().out
    assert "Auto-start" in out
    assert ("Enabled" if expected else "Disabled") in out


def test_cli_flag_reports_a_write_failure(monkeypatch, capsys):
    def fail(_enable):
        raise PermissionError("read-only home")

    monkeypatch.setattr(startup, "set_autostart", fail)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--autostart-enable"])

    assert main.main() == 1
    assert "read-only home" in capsys.readouterr().err


# --- doctor ----------------------------------------------------------------- #
def test_doctor_reports_autostart_status(monkeypatch):
    monkeypatch.setattr(doctor.startup, "status_text", lambda: "Enabled")

    report = doctor.report()

    assert "Auto-start on boot: Enabled" in report
