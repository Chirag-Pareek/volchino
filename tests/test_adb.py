"""Tests for Phase 7: Wireless ADB automation bridge ('Ghost in the Droid' pattern)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from server.android.adb_client import AdbClient, AdbError
from server.config import Settings
from server.permissions import Decision, check
from server.pipeline import deterministic
from server.tools import REGISTRY
from server.tools.base import CmdResult, DryRunRunner, ToolArgError


class MockAdbRunner(DryRunRunner):
    """Custom runner for mocking ADB subprocess outputs."""

    def __init__(self, responses: dict[tuple[str, ...], CmdResult] | None = None) -> None:
        super().__init__()
        self.responses: dict[tuple[str, ...], CmdResult] = responses or {}
        self.installed = {"adb"}

    async def run(self, argv, *, timeout, env=None):
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.responses:
            return self.responses[key]
        # Partial match prefix
        for r_key, r_val in self.responses.items():
            if len(argv) >= len(r_key) and tuple(argv[: len(r_key)]) == r_key:
                return r_val
        return CmdResult(0, "OK", "")


@pytest.fixture
def adb_settings(tmp_path: Path) -> Settings:
    return Settings(
        adb_device_id="100.64.0.5",
        adb_port=5555,
        screenshot_dir=tmp_path / "screenshots",
        tool_timeout_s=5.0,
    )


# ── Connection & Watchdog Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adb_connect_success(adb_settings: Settings):
    runner = MockAdbRunner(
        {
            ("adb", "connect", "100.64.0.5:5555"): CmdResult(
                0, "connected to 100.64.0.5:5555\n", ""
            ),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    assert await client.connect() is True
    assert ["adb", "connect", "100.64.0.5:5555"] in runner.calls


@pytest.mark.asyncio
async def test_adb_connect_failure(adb_settings: Settings):
    runner = MockAdbRunner(
        {
            ("adb", "connect", "100.64.0.5:5555"): CmdResult(
                1, "", "failed to connect to 100.64.0.5:5555"
            ),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    assert await client.connect() is False


@pytest.mark.asyncio
async def test_adb_is_connected(adb_settings: Settings):
    runner = MockAdbRunner(
        {
            ("adb", "devices"): CmdResult(
                0,
                "List of devices attached\n100.64.0.5:5555\tdevice\nemulator-5554\toffline\n",
                "",
            ),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    assert await client.is_connected() is True


@pytest.mark.asyncio
async def test_adb_is_connected_offline(adb_settings: Settings):
    runner = MockAdbRunner(
        {
            ("adb", "devices"): CmdResult(
                0,
                "List of devices attached\n100.64.0.5:5555\toffline\n",
                "",
            ),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    assert await client.is_connected() is False


@pytest.mark.asyncio
async def test_adb_watchdog_auto_reconnect(adb_settings: Settings):
    # First devices call: offline; connect: ok; second devices call: online
    call_count = 0

    class StateTrackingRunner(MockAdbRunner):
        async def run(self, argv, *, timeout, env=None):
            nonlocal call_count
            self.calls.append(list(argv))
            if argv == ["adb", "devices"]:
                call_count += 1
                if call_count == 1:
                    return CmdResult(0, "List of devices attached\n100.64.0.5:5555\toffline\n", "")
                return CmdResult(0, "List of devices attached\n100.64.0.5:5555\tdevice\n", "")
            if argv == ["adb", "connect", "100.64.0.5:5555"]:
                return CmdResult(0, "connected to 100.64.0.5:5555\n", "")
            return CmdResult(0, "OK", "")

    runner = StateTrackingRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    client.start_watchdog(interval_s=0.05)
    await asyncio.sleep(0.12)
    await client.stop_watchdog()

    assert ["adb", "connect", "100.64.0.5:5555"] in runner.calls


# ── Safe Actions (Set A) Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_phone_app_alias(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.open_phone_app("youtube")
    assert "com.google.android.youtube" in res
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "monkey",
        "-p",
        "com.google.android.youtube",
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    ] in runner.calls


@pytest.mark.asyncio
async def test_open_phone_app_activity(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.open_phone_app("spotify", activity=".MainActivity")
    assert "com.spotify.music" in res
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "am",
        "start",
        "-n",
        "com.spotify.music/.MainActivity",
    ] in runner.calls


@pytest.mark.asyncio
async def test_open_phone_app_injection_prevented(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    with pytest.raises(AdbError, match="Invalid phone package"):
        await client.open_phone_app("youtube; rm -rf /")


@pytest.mark.asyncio
async def test_media_controls(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)

    # Play/Pause (keyevent 85)
    res_pp = await client.media_play_pause()
    assert "toggled" in res_pp
    assert ["adb", "-s", "100.64.0.5:5555", "shell", "input", "keyevent", "85"] in runner.calls

    # Next (keyevent 87)
    res_next = await client.media_next()
    assert "next" in res_next
    assert ["adb", "-s", "100.64.0.5:5555", "shell", "input", "keyevent", "87"] in runner.calls

    # Prev (keyevent 88)
    res_prev = await client.media_prev()
    assert "previous" in res_prev
    assert ["adb", "-s", "100.64.0.5:5555", "shell", "input", "keyevent", "88"] in runner.calls


@pytest.mark.asyncio
async def test_set_phone_volume(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.set_phone_volume(50)
    assert "50%" in res
    # 50% maps to stream level 8 (out of 15)
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "cmd",
        "media_session",
        "volume",
        "--stream",
        "3",
        "--set",
        "8",
    ] in runner.calls


@pytest.mark.asyncio
async def test_read_phone_notifications_termux(adb_settings: Settings):
    termux_json = (
        '[{"appName": "WhatsApp", "title": "Chirag", "content": "Meeting in 5 mins"}, '
        '{"appName": "Gmail", "title": "GitHub", "content": "Pull Request approved"}]'
    )
    runner = MockAdbRunner(
        {
            ("adb", "-s", "100.64.0.5:5555", "shell", "termux-notification-list"): CmdResult(
                0, termux_json, ""
            ),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.read_phone_notifications()
    assert "WhatsApp: Chirag — Meeting in 5 mins" in res
    assert "Gmail: GitHub — Pull Request approved" in res


@pytest.mark.asyncio
async def test_read_phone_notifications_dumpsys_fallback(adb_settings: Settings):
    dumpsys_output = """
    NotificationRecord(0x123: pkg=com.whatsapp user=0 id=1 tag=null)
      tickerText=null
      android.title=String (Alex)
      android.text=String (Code looks great!)
    NotificationRecord(0x456: pkg=com.google.android.gm user=0 id=2 tag=null)
      android.title=String (Google Cloud)
      android.text=String (Deployment successful)
    """
    runner = MockAdbRunner(
        {
            ("adb", "-s", "100.64.0.5:5555", "shell", "termux-notification-list"): CmdResult(
                1, "", "not found"
            ),
            (
                "adb",
                "-s",
                "100.64.0.5:5555",
                "shell",
                "dumpsys",
                "notification",
                "--noredact",
            ): CmdResult(0, dumpsys_output, ""),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.read_phone_notifications()
    assert "Whatsapp: Alex — Code looks great!" in res
    assert "Gm: Google Cloud — Deployment successful" in res


@pytest.mark.asyncio
async def test_take_phone_screenshot(adb_settings: Settings, tmp_path: Path):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    dest = tmp_path / "screenshots" / "test_sc.png"
    res = await client.take_phone_screenshot(dest_path=dest)
    assert str(dest) in res
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "screencap",
        "-p",
        "/sdcard/phone_screenshot.png",
    ] in runner.calls
    assert ["adb", "-s", "100.64.0.5:5555", "pull", "/sdcard/phone_screenshot.png", str(dest)] in (
        runner.calls
    )
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "rm",
        "-f",
        "/sdcard/phone_screenshot.png",
    ] in runner.calls


# ── Destructive Actions (Set B) & Permission Guards ───────────────────────────────────


def test_permission_guards():
    # Set A (safe)
    assert check("adb:open_app") is Decision.ALLOW
    assert check("adb:media_control") is Decision.ALLOW
    assert check("adb:set_volume") is Decision.ALLOW
    assert check("adb:get_notifications") is Decision.ALLOW
    assert check("adb:take_screenshot") is Decision.ALLOW

    # Set B (destructive - requires confirmation)
    assert check("adb:uninstall_package") is Decision.CONFIRM
    assert check("adb:reboot") is Decision.CONFIRM
    assert check("adb:run_shell") is Decision.CONFIRM


@pytest.mark.asyncio
async def test_destructive_uninstall(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.uninstall_phone_package("com.example.testapp")
    assert "com.example.testapp" in res
    assert [
        "adb",
        "-s",
        "100.64.0.5:5555",
        "shell",
        "pm",
        "uninstall",
        "com.example.testapp",
    ] in runner.calls


@pytest.mark.asyncio
async def test_destructive_reboot(adb_settings: Settings):
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.reboot_phone()
    assert "reboot" in res.lower()
    assert ["adb", "-s", "100.64.0.5:5555", "reboot"] in runner.calls


@pytest.mark.asyncio
async def test_destructive_shell(adb_settings: Settings):
    runner = MockAdbRunner(
        {
            ("adb", "-s", "100.64.0.5:5555", "shell", "uptime"): CmdResult(0, "up 2 days", ""),
        }
    )
    client = AdbClient(settings=adb_settings, runner=runner)
    res = await client.run_phone_shell("uptime")
    assert "up 2 days" in res


# ── Tool Registry & Arguments Validation ───────────────────────────────────────────────


def test_registry_contains_adb_tools():
    expected_tools = {
        "open_phone_app",
        "media_play_pause",
        "media_next",
        "media_prev",
        "set_phone_volume",
        "get_phone_notifications",
        "take_phone_screenshot",
        "uninstall_phone_package",
        "reboot_phone",
        "run_phone_shell",
    }
    assert expected_tools.issubset(REGISTRY.keys())


def test_open_phone_app_args_validation():
    args = REGISTRY["open_phone_app"].validate({"package": "youtube"})
    assert args.package == "com.google.android.youtube"

    with pytest.raises(ToolArgError):
        REGISTRY["open_phone_app"].validate({"package": "bad package name; injection"})


def test_set_phone_volume_args_validation():
    args = REGISTRY["set_phone_volume"].validate({"level": 75})
    assert args.level == 75

    with pytest.raises(ToolArgError):
        REGISTRY["set_phone_volume"].validate({"level": 150})


# ── Deterministic Zero-Token Speech Routing Tests ──────────────────────────────────────


@pytest.mark.parametrize(
    ("phrase", "expected_tool", "expected_args"),
    [
        ("pause phone music", "media_play_pause", {}),
        ("pause music on phone", "media_play_pause", {}),
        ("phone pause music", "media_play_pause", {}),
        ("next song on phone", "media_next", {}),
        ("phone next track", "media_next", {}),
        ("previous song on phone", "media_prev", {}),
        ("phone prev track", "media_prev", {}),
        ("phone screenshot", "take_phone_screenshot", {}),
        ("take phone screenshot", "take_phone_screenshot", {}),
        ("phone notifications", "get_phone_notifications", {}),
        ("read phone notifications", "get_phone_notifications", {}),
        ("open youtube on phone", "open_phone_app", {"package": "youtube"}),
        ("launch spotify on phone", "open_phone_app", {"package": "spotify"}),
        ("phone volume 50%", "set_phone_volume", {"level": 50}),
        ("set phone volume to 75", "set_phone_volume", {"level": 75}),
        ("mute phone", "set_phone_volume", {"level": 0}),
        ("reboot phone", "reboot_phone", {}),
        ("uninstall com.bad.app on phone", "uninstall_phone_package", {"package": "com.bad.app"}),
    ],
)
def test_deterministic_phone_speech_queries(phrase: str, expected_tool: str, expected_args: dict):
    call = deterministic.match(phrase)
    assert call is not None, f"Phrase '{phrase}' failed to match deterministically"
    assert call.tool == expected_tool
    assert call.args == expected_args


# ── WebSocket UI Status Broadcast Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adb_status_broadcast(adb_settings: Settings):
    broadcast_mock = AsyncMock()
    runner = MockAdbRunner()
    client = AdbClient(settings=adb_settings, runner=runner, broadcast=broadcast_mock)

    await client.media_play_pause()

    # Verify status events emitted
    assert broadcast_mock.await_count >= 2
    exec_call = broadcast_mock.await_args_list[0][0][0]
    succ_call = broadcast_mock.await_args_list[1][0][0]

    assert exec_call["type"] == "adb_status"
    assert exec_call["status"] == "executing"
    assert exec_call["action"] == "media_play_pause"
    assert exec_call["target"] == "phone"

    assert succ_call["type"] == "adb_status"
    assert succ_call["status"] == "success"
    assert succ_call["action"] == "media_play_pause"
