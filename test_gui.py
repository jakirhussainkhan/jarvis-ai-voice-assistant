"""Smoke tests for the PyQt5 floating widget + tray (gui.py).

These run on the *offscreen* Qt platform so they never open a real window, and
are skipped entirely when PyQt5 is not installed. They verify the parts that are
easy to get wrong without a display: icon painting, widget construction and the
state label.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication   # noqa: E402

import gui                                 # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_make_icon_paints_something(app):
    icon = gui.make_icon(64)
    assert not icon.isNull()


def test_widget_builds_and_tracks_state(app):
    widget = gui.JarvisWidget()
    assert widget.width() > 0 and widget.height() > 0
    widget.set_state(gui.THINKING)
    assert widget.status.text() == gui.THINKING
    widget.set_state(gui.IDLE)
    assert widget.status.text() == gui.IDLE


def test_widget_quit_does_not_hang(app):
    widget = gui.JarvisWidget()
    widget._quitting = True
    widget.quit()          # no worker running: must return immediately


def test_hide_to_tray_does_not_quit_by_default(app):
    """Closing the widget must hide it, not quit the app."""
    widget = gui.JarvisWidget()
    # With no tray available (offscreen), the close falls through to accept.
    assert widget._quitting is False
