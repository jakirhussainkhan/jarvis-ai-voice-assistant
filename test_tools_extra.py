"""Tests for the new tools (tools.py) with the heavy calls mocked."""

import pytest

import confirm
import desktop
import net
import shell
import tools
import vision


@pytest.fixture(autouse=True)
def _clear():
    confirm.clear()
    yield
    confirm.clear()


def test_run_command_delegates_to_shell(monkeypatch):
    monkeypatch.setattr(shell, "run", lambda c, d=None: f"ran:{c}:{d}")
    assert tools.fn_run_command("ls", "~") == "ran:ls:~"


def test_run_command_empty_cwd_becomes_none(monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "run", lambda c, d=None: seen.append(d) or "")
    tools.fn_run_command("ls")
    assert seen == [None]


def test_run_in_terminal_is_guarded(monkeypatch):
    assert "won't run" in tools.fn_run_in_terminal("sudo ls").lower()


def test_run_in_terminal_safe_runs(monkeypatch):
    monkeypatch.setattr(desktop, "run_in_terminal", lambda c: f"term:{c}")
    assert tools.fn_run_in_terminal("echo hi") == "term:echo hi"


def test_run_in_terminal_confirms_risky(monkeypatch):
    out = tools.fn_run_in_terminal("rm x")
    assert confirm.is_pending() and "rm x" in out


def test_see_screen_delegates(monkeypatch):
    monkeypatch.setattr(vision, "look", lambda q=None: f"look:{q}")
    assert tools.fn_see_screen("what is this") == "look:what is this"
    assert tools.fn_see_screen("") == "look:None"


def test_screen_do_safe(monkeypatch):
    monkeypatch.setattr(vision, "act", lambda g: f"act:{g}")
    assert tools.fn_screen_do("click play") == "act:click play"


def test_screen_do_risky_confirms(monkeypatch):
    monkeypatch.setattr(vision, "act", lambda g: "acted")
    out = tools.fn_screen_do("delete the file")
    assert confirm.is_pending() and "delete the file" in out
    handled, reply = confirm.resolve("yes")
    assert handled and reply == "acted"


def test_click_and_keys_delegate(monkeypatch):
    monkeypatch.setattr(desktop, "click_at", lambda x, y, button=1, double=False: "clicked")
    monkeypatch.setattr(desktop, "press_keys", lambda k: f"key:{k}")
    assert tools.fn_click(1, 2) == "clicked"
    assert tools.fn_press_keys("ctrl+s") == "key:ctrl+s"


def test_web_lookup_and_weather_delegate(monkeypatch):
    monkeypatch.setattr(net, "format_results", lambda q: f"results:{q}")
    monkeypatch.setattr(net, "weather", lambda c="": f"w:{c}")
    assert tools.fn_web_lookup("news") == "results:news"
    assert tools.fn_weather("Paris") == "w:Paris"


def test_fetch_page_and_wikipedia_delegate(monkeypatch):
    monkeypatch.setattr(net, "fetch_page", lambda u: f"page:{u}")
    monkeypatch.setattr(net, "wikipedia", lambda t: f"wiki:{t}")
    assert tools.fn_fetch_page("http://x") == "page:http://x"
    assert tools.fn_wikipedia("Linux") == "wiki:Linux"


def test_kill_process_needs_confirmation(monkeypatch):
    monkeypatch.setattr(desktop, "matching_process_pids", lambda n: [11, 22])
    stopped = []
    monkeypatch.setattr(desktop, "_terminate",
                        lambda pids, timeout=3.0: stopped.append(pids) or len(pids))
    out = tools.fn_kill_process("vlc")
    assert confirm.is_pending() and "vlc" in out
    handled, reply = confirm.resolve("yes")
    assert handled and stopped == [[11, 22]]


def test_kill_process_not_found(monkeypatch):
    monkeypatch.setattr(desktop, "matching_process_pids", lambda n: [])
    assert "don't see" in tools.fn_kill_process("nope").lower()


def test_notify_and_clipboard_delegate(monkeypatch):
    monkeypatch.setattr(desktop, "notify", lambda m, t="JARVIS": f"n:{m}")
    monkeypatch.setattr(desktop, "clipboard", lambda a: f"c:{a}")
    assert tools.fn_notify("hi") == "n:hi"
    assert tools.fn_clipboard("copy") == "c:copy"


def test_timers_and_reminders_delegate(monkeypatch):
    import timers
    monkeypatch.setattr(timers, "set_timer", lambda s, l="Timer": f"t:{s}")
    monkeypatch.setattr(timers, "set_reminder", lambda t, m=5: f"r:{t}")
    assert tools.fn_set_timer(30) == "t:30"
    assert tools.fn_set_reminder("tea") == "r:tea"


# --------------------------------------------------------------------------- #
# fn_read_file sandbox
# --------------------------------------------------------------------------- #

def test_read_file_default_allowlist_includes_the_project():
    from pathlib import Path
    assert Path(__file__).resolve().parent in tools._read_roots()


def test_read_file_allows_a_file_in_an_allowed_root(tmp_path, monkeypatch):
    note = tmp_path / "note.txt"
    note.write_text("hello world")
    monkeypatch.setattr(tools, "_read_roots", lambda: [tmp_path.resolve()])
    assert tools.fn_read_file(str(note)) == "hello world"


def test_read_file_denies_a_file_outside_the_allowlist(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    monkeypatch.setattr(tools, "_read_roots", lambda: [tmp_path / "allowed"])
    assert tools.fn_read_file(str(secret)) == tools._READ_DENIED


def test_read_file_denies_traversal_escape(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    monkeypatch.setattr(tools, "_read_roots", lambda: [allowed.resolve()])
    # "../outside.txt" resolves to the parent, which is not in the allowlist.
    assert tools.fn_read_file(str(allowed / ".." / "outside.txt")) == tools._READ_DENIED


def test_read_file_truncates_long_files(tmp_path, monkeypatch):
    big = tmp_path / "big.txt"
    big.write_text("x" * (tools._READ_LIMIT + 100))
    monkeypatch.setattr(tools, "_read_roots", lambda: [tmp_path.resolve()])
    out = tools.fn_read_file(str(big))
    assert out.endswith("[truncated]")
    assert out.startswith("x" * 10)


def test_read_file_missing_file_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_read_roots", lambda: [tmp_path.resolve()])
    assert "File not found" in tools.fn_read_file(str(tmp_path / "nope.txt"))

