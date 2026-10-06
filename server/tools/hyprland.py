"""Hyprland desktop tools (spec §4) — all via ``hyprctl dispatch``."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.tools.aliases import resolve_app, resolve_window_class
from server.tools.base import NoArgs, Tool, ToolContext, ToolError, run_checked
from server.tools.session_env import desktop_env

# `hyprctl dispatch exec` hands its argument to a shell inside Hyprland, so the binary name is
# restricted to a conservative charset and must resolve on PATH.
_BINARY_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
_CLASS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class OpenAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(description="Application name or alias, e.g. 'firefox', 'vs code'")

    @field_validator("app")
    @classmethod
    def _resolve(cls, v: str) -> str:
        binary = resolve_app(v)
        if not _BINARY_RE.fullmatch(binary):
            raise ValueError(f"unsupported application name {v!r}")
        return binary


class WorkspaceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num: int = Field(ge=1, le=20, description="Workspace number 1-20")


class FocusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_class: str = Field(description="Window class or app alias, e.g. 'obsidian'")

    @field_validator("window_class")
    @classmethod
    def _resolve(cls, v: str) -> str:
        klass = resolve_window_class(v)
        if not _CLASS_RE.fullmatch(klass):
            raise ValueError(f"unsupported window class {v!r}")
        return klass


async def _dispatch(ctx: ToolContext, *args: str) -> None:
    await run_checked(ctx, ["hyprctl", "dispatch", *args], env=desktop_env())


async def open_app(args: OpenAppArgs, ctx: ToolContext) -> str:
    if ctx.runner.which(args.app) is None:
        raise ToolError(f"{args.app} is not installed")
    await _dispatch(ctx, "exec", args.app)
    return f"Opening {args.app}."


async def close_window(_: NoArgs, ctx: ToolContext) -> str:
    await _dispatch(ctx, "killactive")
    return "Closed the active window."


async def switch_workspace(args: WorkspaceArgs, ctx: ToolContext) -> str:
    await _dispatch(ctx, "workspace", str(args.num))
    return f"Switched to workspace {args.num}."


async def focus_window(args: FocusArgs, ctx: ToolContext) -> str:
    await _dispatch(ctx, "focuswindow", f"class:{args.window_class}")
    return f"Focused {args.window_class}."


TOOLS = [
    Tool("open_app", "hyprctl:open_app", "Launch an application", OpenAppArgs, open_app),
    Tool("close_window", "hyprctl:close_window", "Close the active window", NoArgs, close_window),
    Tool(
        "switch_workspace",
        "hyprctl:switch_workspace",
        "Switch Hyprland workspace",
        WorkspaceArgs,
        switch_workspace,
    ),
    Tool(
        "focus_window",
        "hyprctl:focus_window",
        "Focus a window by class",
        FocusArgs,
        focus_window,
    ),
]
