"""Test deterministic regex matching."""

from server.pipeline.deterministic import match


def test_volume():
    c = match("volume 30%")
    assert c is not None
    assert c.tool == "set_volume"
    assert c.args == {"level": 30}


def test_set_volume_to():
    c = match("set volume to 50%")
    assert c is not None
    assert c.tool == "set_volume"
    assert c.args == {"level": 50}


def test_mute():
    c = match("mute")
    assert c is not None
    assert c.tool == "set_volume"
    assert c.args == {"level": 0}


def test_screenshot():
    c = match("take a screenshot")
    assert c is not None
    assert c.tool == "take_screenshot"


def test_close_window():
    c = match("close window")
    assert c is not None
    assert c.tool == "close_window"


def test_switch_workspace():
    c = match("workspace 3")
    assert c is not None
    assert c.tool == "switch_workspace"
    assert c.args == {"num": 3}


def test_battery():
    c = match("battery level")
    assert c is not None
    assert c.tool == "get_battery_status"


def test_work_time():
    c = match("what's my work time today")
    assert c is not None
    assert c.tool == "get_work_time_today"


def test_git_status():
    c = match("git status")
    assert c is not None
    assert c.tool == "get_git_status"


def test_open_firefox():
    c = match("open firefox")
    assert c is not None
    assert c.tool == "open_app"
    assert c.args == {"app": "firefox"}


def test_open_vs_code():
    c = match("open vs code")
    assert c is not None
    assert c.tool == "open_app"
    assert c.args == {"app": "vs code"}  # alias resolved in the tool validator


def test_open_github():
    c = match("open github")
    assert c is not None
    assert c.tool == "open_url"
    assert c.args == {"url": "github"}


def test_focus():
    c = match("focus obsidian")
    assert c is not None
    assert c.tool == "focus_window"


def test_no_match():
    assert match("tell me a joke") is None
