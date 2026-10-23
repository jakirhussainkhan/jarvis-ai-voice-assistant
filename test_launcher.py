"""Tests for the desktop launcher (`run.sh`, `install_desktop.py`, `assets/`).

Nothing here opens a window or touches the real home directory: every install
writes into a temporary "home" passed in explicitly, and the menu cache refresh
is stubbed out. When `desktop-file-validate` is available the generated entry is
validated with it -- the same check the desktop environment performs.
"""

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

import install_desktop

ROOT = Path(install_desktop.__file__).resolve().parent
APPS = Path(".local/share/applications/jarvis.desktop")
AUTOSTART = Path(".config/autostart/jarvis.desktop")


@pytest.fixture(autouse=True)
def _no_menu_cache(monkeypatch):
    """Keep the tests hermetic: never shell out to update-desktop-database."""
    monkeypatch.setattr(install_desktop, "_refresh_menu_cache", lambda _apps: None)


def _png_size(path):
    """(width, height) of a PNG, and a real PNG signature check."""
    header = Path(path).read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


# --------------------------------------------------------------------------- #
# run.sh
# --------------------------------------------------------------------------- #

def test_run_sh_starts_the_gui():
    script = ROOT / "run.sh"
    assert script.exists()
    text = script.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "main.py --gui" in text
    assert "exec " in text, "the GUI should replace the shell, not nest inside it"


@pytest.mark.skipif(os.name == "nt",
                    reason="run.sh is the POSIX launcher; Windows uses start.bat")
def test_run_sh_explains_a_missing_virtualenv(tmp_path):
    shutil.copy(ROOT / "run.sh", tmp_path / "run.sh")

    result = subprocess.run(["bash", str(tmp_path / "run.sh")],
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == 1
    assert "no virtualenv" in result.stderr
    assert "pip install -r requirements.txt" in result.stderr


# --------------------------------------------------------------------------- #
# the icon
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size", [32, 48, 64, 128, 256])
def test_every_icon_is_a_real_png_of_the_right_size(size):
    path = ROOT / "assets" / f"jarvis-{size}.png"
    assert path.exists(), f"{path} is missing"
    assert _png_size(path) == (size, size)
    assert path.stat().st_size > 200


# --------------------------------------------------------------------------- #
# install_desktop: where things are written
# --------------------------------------------------------------------------- #

def test_entry_dirs_default_to_the_real_home():
    apps, autostart = install_desktop.entry_dirs()
    assert apps == Path.home() / APPS.parent
    assert autostart == Path.home() / AUTOSTART.parent


def test_install_writes_a_menu_entry(tmp_path):
    written = install_desktop.install(home=tmp_path)

    assert written == [tmp_path / APPS]
    text = written[0].read_text()
    assert text.startswith("[Desktop Entry]")
    assert "Type=Application" in text
    assert "Name=JARVIS" in text
    assert "Terminal=false" in text, "a GUI entry must not ask for a terminal"


@pytest.mark.skipif(os.name == "nt",
                    reason=".desktop entries are a Linux format; Windows uses Run")
def test_the_entry_points_at_this_checkout(tmp_path):
    text = install_desktop.install(home=tmp_path)[0].read_text()

    assert f"Exec={install_desktop.quote_argument(install_desktop.LAUNCHER)}" in text
    assert f"Icon={install_desktop.ICON}" in text
    assert f"Path={ROOT}" in text
    assert install_desktop.LAUNCHER.exists()
    assert install_desktop.ICON.exists()


def test_autostart_is_opt_in(tmp_path):
    install_desktop.install(home=tmp_path)
    assert not (tmp_path / AUTOSTART).exists(), "autostart must not be assumed"

    install_desktop.uninstall(home=tmp_path)
    written = install_desktop.install(home=tmp_path, autostart=True)

    assert len(written) == 2
    assert (tmp_path / AUTOSTART).exists()
    assert "X-GNOME-Autostart-enabled=true" in (tmp_path / AUTOSTART).read_text()


# --------------------------------------------------------------------------- #
# install_desktop: uninstall is exact and safe
# --------------------------------------------------------------------------- #

def test_uninstall_removes_exactly_its_own_files(tmp_path):
    install_desktop.install(home=tmp_path, autostart=True)
    app_dir = tmp_path / APPS.parent
    keep = app_dir / "somebody-elses.desktop"
    keep.write_text("[Desktop Entry]\nName=Not Jarvis\n")

    removed = install_desktop.uninstall(home=tmp_path)

    assert len(removed) == 2
    assert not (tmp_path / APPS).exists()
    assert not (tmp_path / AUTOSTART).exists()
    assert keep.exists(), "another app's entry must not be touched"


def test_uninstall_is_safe_when_nothing_is_installed(tmp_path, capsys):
    assert install_desktop.uninstall(home=tmp_path) == []

    assert install_desktop.main(["--home", str(tmp_path), "--uninstall"]) == 0
    assert "Nothing to remove" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# install_desktop: the command line
# --------------------------------------------------------------------------- #

def test_main_installs_into_the_given_home(tmp_path, capsys):
    assert install_desktop.main(["--home", str(tmp_path)]) == 0

    assert (tmp_path / APPS).exists()
    out = capsys.readouterr().out
    assert "Installed:" in out
    assert "--uninstall" in out, "it should say how to undo"


def test_main_install_then_uninstall_round_trips(tmp_path, capsys):
    install_desktop.main(["--home", str(tmp_path)])
    capsys.readouterr()

    assert install_desktop.main(["--home", str(tmp_path), "--uninstall"]) == 0

    assert not (tmp_path / APPS).exists()
    assert "Removed:" in capsys.readouterr().out


def test_main_fails_clearly_without_a_launcher(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install_desktop, "LAUNCHER", tmp_path / "gone.sh")

    assert install_desktop.main(["--home", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().out


@pytest.mark.parametrize("value,expected", [
    ("/plain/path", "/plain/path"),
    ("/with space/run.sh", '"/with space/run.sh"'),
    ('/odd"quote', '"/odd\\"quote"'),
    ("/100%/run.sh", "/100%%/run.sh"),
])
def test_exec_arguments_are_quoted_for_the_desktop_spec(value, expected):
    assert install_desktop.quote_argument(value) == expected


def test_the_entry_passes_desktop_file_validate(tmp_path):
    validator = shutil.which("desktop-file-validate")
    if not validator:
        pytest.skip("desktop-file-validate is not installed")

    path = install_desktop.install(home=tmp_path)[0]
    result = subprocess.run([validator, str(path)], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
