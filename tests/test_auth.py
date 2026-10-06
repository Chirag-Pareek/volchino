"""Test auth module."""

from server.auth import token_valid


def test_valid_token():
    assert token_valid("secret123", "secret123") is True


def test_wrong_token():
    assert token_valid("wrong", "secret123") is False


def test_none_token():
    assert token_valid(None, "secret123") is False


def test_empty_configured():
    assert token_valid("anything", "") is False


def test_both_empty():
    assert token_valid("", "") is False
