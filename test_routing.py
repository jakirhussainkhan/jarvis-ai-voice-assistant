"""Tests for the offline router (router.try_local)."""

import router


def _patch_side_effects(monkeypatch):
    """Stop the router launching apps / opening browsers during tests."""
    monkeypatch.setattr(router, "_open", lambda url: True)
    monkeypatch.setattr(router, "_launch", lambda candidates: True)
    monkeypatch.setattr(router, "_run", lambda args: True)


def test_empty_and_blank_are_not_handled():
    assert router.try_local("") is None
    assert router.try_local("   ") is None


def test_time_is_handled_locally():
    reply = router.try_local("what time is it")
    assert reply is not None
    assert reply.startswith("Current time:")


def test_system_status_is_handled_locally():
    reply = router.try_local("system status")
    assert reply is not None
    assert reply.startswith("CPU ")


def test_date_is_handled_locally():
    reply = router.try_local("what's the date today")
    assert reply is not None
    assert reply.startswith("Today is ")


def test_open_youtube_stays_local(monkeypatch):
    _patch_side_effects(monkeypatch)
    assert router.try_local("open youtube") == "Opening YouTube."


def test_play_on_youtube_keeps_query_local(monkeypatch):
    _patch_side_effects(monkeypatch)
    assert router.try_local("play lo-fi music on youtube") == "Playing lo-fi music on YouTube."


def test_open_app_is_local(monkeypatch):
    _patch_side_effects(monkeypatch)
    assert router.try_local("open chrome") == "Opening Chrome."


def test_search_verb_defers_to_agent(monkeypatch):
    _patch_side_effects(monkeypatch)
    assert router.try_local("open YouTube and search for lo-fi music") is None
    assert router.try_local("search for cats") is None
    assert router.try_local("look up the weather") is None
    assert router.try_local("find my notes") is None


def test_no_bengali_helpers_remain():
    removed = ("_has_bengali", "_reply", "_digits", "_BN_DIGITS",
               "_BN_WEEKDAYS", "_BN_MONTHS", "_APP_BN")
    for name in removed:
        assert not hasattr(router, name), f"{name} should have been removed"
