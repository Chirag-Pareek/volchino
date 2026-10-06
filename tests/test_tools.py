"""Test tool argument validation."""

import pytest

from server.tools import REGISTRY
from server.tools.base import ToolArgError


def test_set_volume_valid():
    args = REGISTRY["set_volume"].validate({"level": 50})
    assert args.level == 50


def test_set_volume_out_of_range():
    with pytest.raises(ToolArgError, match="level"):
        REGISTRY["set_volume"].validate({"level": 200})


def test_set_volume_missing():
    with pytest.raises(ToolArgError, match="level"):
        REGISTRY["set_volume"].validate({})


def test_open_app_valid():
    args = REGISTRY["open_app"].validate({"app": "firefox"})
    assert args.app == "firefox"


def test_open_app_alias():
    args = REGISTRY["open_app"].validate({"app": "vs code"})
    assert args.app == "code"  # resolved


def test_open_app_shell_injection():
    with pytest.raises(ToolArgError):
        REGISTRY["open_app"].validate({"app": "firefox; rm -rf /"})


def test_switch_workspace_valid():
    args = REGISTRY["switch_workspace"].validate({"num": 5})
    assert args.num == 5


def test_switch_workspace_too_high():
    with pytest.raises(ToolArgError):
        REGISTRY["switch_workspace"].validate({"num": 99})


def test_open_url_alias():
    args = REGISTRY["open_url"].validate({"url": "github"})
    assert args.url == "https://github.com"


def test_open_url_shell_injection():
    with pytest.raises(ToolArgError):
        REGISTRY["open_url"].validate({"url": "file:///etc/passwd"})


def test_extra_args_rejected():
    with pytest.raises(ToolArgError):
        REGISTRY["close_window"].validate({"unexpected": True})


def test_git_status_dash_injection():
    with pytest.raises(ToolArgError):
        REGISTRY["get_git_status"].validate({"repo": "--exec=evil"})
