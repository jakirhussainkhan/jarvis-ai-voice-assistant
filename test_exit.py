"""Tests for main.is_exit / main.is_shutdown (session and app exit detection)."""

from main import is_exit, is_shutdown


def test_exit_words_are_detected():
    assert is_exit("exit")
    assert is_exit("quit")
    assert is_exit("bye")
    assert is_exit("please stop")
    assert is_exit("okay bye now")


def test_word_boundaries_prevent_false_positives():
    assert not is_exit("unstoppable")   # 'stop' inside a word
    assert not is_exit("byebye")        # no standalone 'bye'
    assert not is_exit("restart")       # no exit word at all


def test_old_bengali_exit_words_are_gone():
    assert not is_exit("bondho")
    assert not is_exit("thamo")


def test_exit_word_with_filler_still_exits():
    assert is_exit("stop it")
    assert is_exit("quit now")


def test_exit_word_with_a_target_is_not_exit():
    # "quit/stop X" is a command about X, so it must reach the router.
    assert not is_exit("quit youtube")
    assert not is_exit("stop the music")
    assert not is_exit("close youtube")


def test_explicit_close_commands_exit():
    for phrase in (
        "close yourself",
        "jarvis close yourself",
        "shut yourself down",
        "shut down jarvis",
        "shutdown jarvis",
        "exit jarvis",
        "quit jarvis",
        "terminate yourself",
        "close jarvis",
    ):
        assert is_exit(phrase), phrase
        assert is_shutdown(phrase), phrase


def test_shutdown_phrases_do_not_swallow_real_commands():
    assert not is_shutdown("close youtube")
    assert not is_shutdown("shut down the computer")
    assert not is_shutdown("quit youtube")
    assert is_exit("stop")                       # a normal session exit
    assert not is_shutdown("stop")               # but not an app close
