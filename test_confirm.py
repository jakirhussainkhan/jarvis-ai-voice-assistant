"""Tests for the confirmation flow (confirm.py)."""

import pytest

import confirm


@pytest.fixture(autouse=True)
def _clear():
    confirm.clear()
    yield
    confirm.clear()


def test_yes_runs_the_action():
    ran = []
    confirm.request(lambda: ran.append(True) or "Done.", "Are you sure?")
    assert confirm.resolve("yes") == (True, "Done.")
    assert ran == [True]
    assert not confirm.is_pending()


def test_other_reply_cancels():
    ran = []
    confirm.request(lambda: ran.append(True) or "Done.", "Are you sure?")
    assert confirm.resolve("no") == (True, "Cancelled.")
    assert ran == []


def test_timeout_does_not_run_the_action():
    ran = []
    confirm.request(lambda: ran.append(True) or "Done.", "Are you sure?")
    later = __import__("time").monotonic() + confirm._TTL + 1
    assert confirm.resolve("yes", now=later) == (True, "That request expired.")
    assert ran == []


def test_resolve_without_pending_is_unhandled():
    assert confirm.resolve("yes") == (False, None)


def test_ask_action_is_a_prompt_first_alias_of_request():
    ran = []
    assert confirm.ask_action(
        "Are you sure you want to shut down?", lambda: ran.append(True) or "Down."
    ) == "Are you sure you want to shut down?"
    assert confirm.is_pending()
    assert confirm.resolve("yes") == (True, "Down.")
    assert ran == [True]


def test_is_yes_accepts_common_affirmatives():
    for word in ("yes", "Yeah", "yep", "sure", "ok", "okay", "do it"):
        assert confirm.is_yes(word), word


def test_is_yes_rejects_anything_else():
    for word in ("no", "nope", "not now", "cancel", "", "maybe"):
        assert not confirm.is_yes(word), word
