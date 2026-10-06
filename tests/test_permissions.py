"""Test permissions module."""

from server.permissions import Decision, check, check_all


def test_safe_tool():
    assert check("hyprctl:open_app") is Decision.ALLOW


def test_set_b_tool():
    assert check("social:publish_linkedin") is Decision.CONFIRM


def test_unknown_tag():
    assert check("unknown:something") is Decision.CONFIRM


def test_check_all_safe():
    assert check_all(["hyprctl:open_app", "system:set_volume"]) is Decision.ALLOW


def test_check_all_mixed():
    assert check_all(["hyprctl:open_app", "git:push"]) is Decision.CONFIRM


def test_check_all_empty():
    assert check_all([]) is Decision.CONFIRM
