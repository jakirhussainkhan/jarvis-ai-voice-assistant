"""Tests for main.is_exit (session exit detection)."""

from main import is_exit


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
