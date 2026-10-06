"""Test normalize stage."""

from server.pipeline.normalize import normalize


def test_strips_wake_word():
    assert normalize("Hey Volchino, open Firefox") == "open firefox"


def test_strips_please():
    assert normalize("Please set volume to 50%, thanks") == "set volume to 50%"


def test_percent_spacing():
    assert normalize("volume 30 per cent") == "volume 30%"
    assert normalize("volume 30 %") == "volume 30%"


def test_unicode():
    assert normalize("what\u2019s my work time today?") == "what's my work time today"


def test_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""
