"""Android / ADB tools (Phase 7): app launch, media, volume, notifications, screenshot, etc."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.android.adb_client import AdbClient, AdbError, resolve_phone_app
from server.tools.base import NoArgs, Tool, ToolContext, ToolError

_APP_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.]+$")
_PKG_RE = re.compile(r"^[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+$")


def _get_client(ctx: ToolContext) -> AdbClient:
    if ctx.adb is not None and isinstance(ctx.adb, AdbClient):
        return ctx.adb
    return AdbClient(settings=ctx.settings, runner=ctx.runner, broadcast=ctx.broadcast)


async def _run_adb_action(coro) -> str:
    try:
        return await coro
    except AdbError as e:
        raise ToolError(str(e)) from e


class OpenPhoneAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(description="Package name or app alias, e.g. 'youtube', 'spotify'")
    activity: str | None = Field(default=None, description="Optional activity name to launch")

    @field_validator("package")
    @classmethod
    def _validate_package(cls, v: str) -> str:
        v = v.strip()
        resolved = resolve_phone_app(v)
        if not _APP_TOKEN_RE.fullmatch(resolved):
            raise ValueError(f"invalid application or package name: {v!r}")
        return resolved

    @field_validator("activity")
    @classmethod
    def _validate_activity(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not _APP_TOKEN_RE.fullmatch(v):
            raise ValueError(f"invalid activity name: {v!r}")
        return v


class PhoneVolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(ge=0, le=100, description="Phone media volume percent 0-100")


class UninstallPhonePackageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(description="Package name to uninstall, e.g. 'com.example.app'")

    @field_validator("package")
    @classmethod
    def _validate_package(cls, v: str) -> str:
        v = v.strip()
        resolved = resolve_phone_app(v)
        if not _PKG_RE.fullmatch(resolved):
            raise ValueError(f"invalid package name format: {v!r}")
        return resolved


class PhoneShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(description="Shell command string to execute on the phone")

    @field_validator("command")
    @classmethod
    def _validate_command(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("command cannot be empty")
        return v


# -- Tool handlers -------------------------------------------------------------------


async def open_phone_app(args: OpenPhoneAppArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.open_phone_app(args.package, activity=args.activity))


async def media_play_pause(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.media_play_pause())


async def media_next(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.media_next())


async def media_prev(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.media_prev())


async def set_phone_volume(args: PhoneVolumeArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.set_phone_volume(args.level))


async def get_phone_notifications(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.read_phone_notifications())


async def take_phone_screenshot(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.take_phone_screenshot())


async def uninstall_phone_package(args: UninstallPhonePackageArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.uninstall_phone_package(args.package))


async def reboot_phone(_: NoArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.reboot_phone())


async def run_phone_shell(args: PhoneShellArgs, ctx: ToolContext) -> str:
    client = _get_client(ctx)
    return await _run_adb_action(client.run_phone_shell(args.command))


TOOLS = [
    # Safe Actions (Set A)
    Tool(
        "open_phone_app",
        "adb:open_app",
        "Launch an application on connected phone",
        OpenPhoneAppArgs,
        open_phone_app,
    ),
    Tool(
        "media_play_pause",
        "adb:media_control",
        "Toggle media play/pause on phone",
        NoArgs,
        media_play_pause,
    ),
    Tool(
        "media_next",
        "adb:media_control",
        "Skip to next track on phone",
        NoArgs,
        media_next,
    ),
    Tool(
        "media_prev",
        "adb:media_control",
        "Skip to previous track on phone",
        NoArgs,
        media_prev,
    ),
    Tool(
        "set_phone_volume",
        "adb:set_volume",
        "Set phone volume percentage 0-100",
        PhoneVolumeArgs,
        set_phone_volume,
    ),
    Tool(
        "get_phone_notifications",
        "adb:get_notifications",
        "Read notifications from phone",
        NoArgs,
        get_phone_notifications,
        cache_ttl_s=10,
    ),
    Tool(
        "take_phone_screenshot",
        "adb:take_screenshot",
        "Take screenshot of phone screen and pull to laptop",
        NoArgs,
        take_phone_screenshot,
    ),
    # Destructive Actions (Set B - Requires Confirmation)
    Tool(
        "uninstall_phone_package",
        "adb:uninstall_package",
        "Uninstall an app package from phone",
        UninstallPhonePackageArgs,
        uninstall_phone_package,
    ),
    Tool(
        "reboot_phone",
        "adb:reboot",
        "Reboot the connected phone",
        NoArgs,
        reboot_phone,
    ),
    Tool(
        "run_phone_shell",
        "adb:run_shell",
        "Execute shell command on phone",
        PhoneShellArgs,
        run_phone_shell,
    ),
]
