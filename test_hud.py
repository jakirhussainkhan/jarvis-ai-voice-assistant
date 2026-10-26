"""Focused tests for the HUD presentation layer (hud.py) and typed commands.

The HUD must never invent data: these tests pin down the honest unavailable
states, the real SQLite transcript seeding, the activity stream and the
measured round-trip reported by :class:`gui.TextCommandWorker`.

Like ``test_gui.py`` this runs on the offscreen Qt platform and is skipped when
PyQt5 is missing.
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt                      # noqa: E402
from PyQt5.QtWidgets import QApplication         # noqa: E402

import gui                                        # noqa: E402
import hud                                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


# --------------------------------------------------------------------------- #
# design-system panels
# --------------------------------------------------------------------------- #

def test_metric_value_is_clamped_and_can_report_unavailable(app):
    metric = hud.HudMetric("Test")
    metric.set_value(2.4, "240%", animate=False)
    assert metric._value == 1.0
    metric.set_value(-3, animate=False)
    assert metric._value == 0.0
    metric.set_unavailable()
    assert metric._display == "UNAVAILABLE"


def test_system_panel_reports_missing_metrics_honestly(app):
    panel = hud.SystemPanel()
    panel.update_system(cpu=None, memory=None, disk=None, net_rate=None,
                        uptime=None, processes=None, battery=None,
                        latency_ms=None)
    assert panel.cpu._display == "UNAVAILABLE"
    assert panel.memory._display == "UNAVAILABLE"
    assert panel.uptime_value.text() == "—"
    assert panel.proc_value.text() == "—"
    assert panel.latency_value.text() == "—"

    panel.update_system(cpu=42, memory=37, disk=55, net_rate=1024,
                        uptime=3661, processes=123, battery="88%",
                        latency_ms=143)
    assert panel.cpu._display == "42%"
    assert panel.memory._display == "37%"
    assert panel.uptime_value.text() == "01:01"
    assert panel.proc_value.text() == "123"
    assert panel.latency_value.text() == "143 ms"


def test_sparkline_only_records_real_samples(app):
    spark = hud.HudSparkline(samples=4)
    spark.set_value("nonsense")           # ignored, never invented
    assert len(spark._values) == 0
    for value in (10, 20, 30, 40, 50):
        spark.set_value(value)
    assert list(spark._values) == [20.0, 30.0, 40.0, 50.0]
    spark.set_unavailable()
    assert len(spark._values) == 0


def test_system_panel_feeds_the_histogram_from_cpu(app):
    panel = hud.SystemPanel()
    panel.update_system(cpu=50)
    assert list(panel.spark._values) == [50.0]


def test_inbox_panel_is_honest_about_no_source(app):
    panel = hud.InboxPanel()
    assert panel.count.text() == "—"
    assert "NOT CONFIGURED" in panel.note.text()
    panel.set_messages([])
    assert panel.count.text() == "—"


def test_transcript_hides_empty_state_and_seeds_history_once(app):
    panel = hud.TranscriptPanel()
    panel.resize(320, 300)
    assert not panel.empty.isHidden()
    panel.append_turn("YOU", "open youtube")
    panel.append_turn("JARVIS", "Opening YouTube.")
    assert panel.empty.isHidden()
    assert panel._message_count == 2

    panel.set_history([("YOU", "old question"), ("JARVIS", "old answer")])
    assert panel._message_count == 2, "history must not duplicate live turns"

    panel.clear()
    assert panel._message_count == 0
    assert not panel.empty.isHidden()


def test_transcript_seeds_from_history_when_empty(app):
    panel = hud.TranscriptPanel()
    panel.set_history([("YOU", "hello"), ("JARVIS", "hi")])
    assert panel._message_count == 2
    assert panel.empty.isHidden()


def test_transcript_facts_never_invent_entries(app):
    panel = hud.TranscriptPanel()
    panel.set_facts([])
    assert "no saved facts" in panel.facts.text()
    panel.set_facts(["I love coding", "My name is Jarvis", "third", "fourth"])
    text = panel.facts.text()
    assert "I LOVE CODING" in text and "MY NAME IS JARVIS" in text
    assert "FOURTH" not in text, "only three facts are shown"


def test_activity_log_escapes_html_and_orders_newest_last(app):
    panel = hud.ActivityPanel()
    panel.log("<b>danger</b>", "error")
    panel.log("plain event", "info")
    text = panel.view.toPlainText()
    assert "<b>danger</b>" in text          # escaped text, not rendered tags
    assert text.index("danger") < text.index("plain event")


def test_command_bar_emits_trimmed_text_and_ignores_blanks(app):
    bar = hud.CommandBar()
    received = []
    bar.submitted.connect(received.append)

    bar.input.setText("   open youtube   ")
    bar._emit()
    assert received == ["open youtube"]
    assert bar.input.text() == ""

    bar._emit()
    assert received == ["open youtube"], "blank input must not submit"

    bar.set_busy(True)
    bar.input.setText("ignored")
    bar._emit()
    assert received == ["open youtube"]


def test_github_panel_shows_local_state_without_a_token(app, monkeypatch):
    monkeypatch.delenv("JARVIS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    panel = hud.GithubPanel()
    try:
        panel.apply({
            "repo": "jarvis", "branch": "feat/hud", "dirty": 2,
            "commit": "abc1234", "subject": "add HUD", "when": "1 minute ago",
            "live": False, "error": "token absent",
        })
        assert panel.repo.text() == "jarvis"
        assert "FEAT/HUD" in panel.branch.text()
        assert "+2 MODIFIED" in panel.branch.text()
        assert "abc1234" in panel.commit.text()
        assert "TOKEN ABSENT" in panel.remote.text()
        assert panel.header.tag.text() == "LOCAL"
    finally:
        panel.stop()


def test_github_panel_marks_remote_live_only_when_probe_says_so(app):
    panel = hud.GithubPanel()
    try:
        panel.apply({
            "repo": "jarvis", "branch": "main", "dirty": 0,
            "commit": "deadbee", "subject": "release", "when": "now",
            "live": True, "notifications": 3, "stars": 9, "issues": 1,
        })
        assert panel.header.tag.text() == "LIVE"
        assert "3 NOTIFICATIONS" in panel.remote.text()
        assert "9 STARS" in panel.remote.text()
    finally:
        panel.stop()


# --------------------------------------------------------------------------- #
# typed command worker + widget wiring
# --------------------------------------------------------------------------- #

def test_text_worker_reports_measured_latency_and_reply(app, monkeypatch):
    monkeypatch.setattr(gui, "handle",
                        lambda text, memory: ("Done.", "local", 0))
    seen = {"state": [], "reply": [], "latency": [], "kind": []}
    worker = gui.TextCommandWorker(None, "status")
    worker.state_changed.connect(seen["state"].append)
    worker.replied.connect(seen["reply"].append)
    worker.latency.connect(seen["latency"].append)
    worker.kind.connect(seen["kind"].append)

    worker.run()                              # synchronous: no thread needed

    assert seen["reply"] == ["Done."]
    assert seen["kind"] == ["local"]
    assert seen["latency"] and seen["latency"][0] >= 0.0
    assert gui.THINKING in seen["state"] and seen["state"][-1] in (
        gui.IDLE, gui.MUTED
    )


def test_text_worker_turns_an_exception_into_an_error(app, monkeypatch):
    def boom(text, memory):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(gui, "handle", boom)
    failures = []
    worker = gui.TextCommandWorker(None, "status")
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ["router exploded"]


def test_widget_command_bar_is_wired_to_the_typed_path(app):
    widget = gui.JarvisWidget()
    try:
        received = []
        widget.command_bar.submitted.disconnect()
        widget.command_bar.submitted.connect(received.append)
        widget.command_bar.input.setText("hello")
        widget.command_bar._emit()
        assert received == ["hello"]
    finally:
        widget._quitting = True
        widget.close()


def test_shutdown_phrase_in_command_bar_uses_the_guarded_quit(app):
    widget = gui.JarvisWidget()
    calls = []
    try:
        import config
        phrase = getattr(config, "SHUTDOWN_PHRASE", "shutdown jarvis")
        widget.quit = lambda: calls.append(True)
        widget._submit_text(phrase)
        deadline = time.monotonic() + 2.0
        while not calls and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert calls, "an explicit shutdown phrase must reach the quit path"
    finally:
        widget._quitting = True
        widget.close()
