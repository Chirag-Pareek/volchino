"""Linux system tools (spec §4): screenshot, volume, battery, git status, open URL."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.tools.aliases import URL_ALIASES
from server.tools.base import NoArgs, Tool, ToolContext, ToolError, run_checked
from server.tools.session_env import desktop_env

_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", re.IGNORECASE)


class VolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(ge=0, le=100, description="Volume percent 0-100")


class GitStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str | None = Field(default=None, description="Path to a git repository (optional)")

    @field_validator("repo")
    @classmethod
    def _path(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if "\x00" in v or v.strip().startswith("-"):
            raise ValueError("invalid repository path")
        return v.strip()


class OpenUrlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="http(s) URL, bare domain or a site alias like 'github'")

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        alias = URL_ALIASES.get(v.lower())
        if alias:
            return alias
        if len(v) > 2048 or any(c.isspace() or ord(c) < 32 for c in v):
            raise ValueError("invalid URL")
        if "://" not in v and _DOMAIN_RE.fullmatch(v):
            v = f"https://{v}"
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("only http(s) URLs are allowed")
        return v


async def take_screenshot(_: NoArgs, ctx: ToolContext) -> str:
    out_dir = ctx.settings.screenshot_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"sc_{datetime.now():%Y%m%d_%H%M%S}.png"
    await run_checked(ctx, ["grim", str(path)], env=desktop_env())
    return f"Screenshot saved to {path}."


async def set_volume(args: VolumeArgs, ctx: ToolContext) -> str:
    await run_checked(
        ctx,
        ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{args.level}%"],
        env=desktop_env(),
    )
    return f"Volume set to {args.level}%."


async def get_battery_status(_: NoArgs, ctx: ToolContext) -> str:
    batteries = sorted(ctx.settings.power_supply_dir.glob("BAT*"))
    if not batteries:
        raise ToolError("No battery found.")
    parts = []
    for bat in batteries:
        try:
            cap = (bat / "capacity").read_text().strip()
            status = (bat / "status").read_text().strip() if (bat / "status").exists() else "?"
        except OSError as e:
            raise ToolError(f"Could not read {bat.name}: {e.strerror}") from e
        parts.append(f"{bat.name}: {cap}% ({status.lower()})")
    return "Battery " + ", ".join(parts) + "."


async def get_git_status(args: GitStatusArgs, ctx: ToolContext) -> str:
    repo = Path(args.repo).expanduser() if args.repo else ctx.settings.default_repo
    repo = repo.resolve()
    if not repo.is_dir():
        raise ToolError(f"{repo} is not a directory.")
    res = await run_checked(ctx, ["git", "-C", str(repo), "status", "--short", "--branch"])
    lines = res.stdout.splitlines()
    branch = lines[0].removeprefix("## ") if lines and lines[0].startswith("##") else "?"
    changes = [ln for ln in lines if not ln.startswith("##")]
    if not changes:
        return f"{repo.name} ({branch}): clean."
    return f"{repo.name} ({branch}): {len(changes)} changed\n" + "\n".join(changes[:20])


async def open_url(args: OpenUrlArgs, ctx: ToolContext) -> str:
    await run_checked(ctx, ["xdg-open", args.url], env=desktop_env())
    return f"Opening {args.url}."


TOOLS = [
    Tool("take_screenshot", "system:take_screenshot", "Take a screenshot", NoArgs, take_screenshot),
    Tool("set_volume", "system:set_volume", "Set output volume percent", VolumeArgs, set_volume),
    Tool(
        "get_battery_status",
        "system:get_battery",
        "Battery level and status",
        NoArgs,
        get_battery_status,
        cache_ttl_s=30,
    ),
    Tool(
        "get_git_status",
        "system:git_status",
        "Short git status of a repo",
        GitStatusArgs,
        get_git_status,
        cache_ttl_s=10,
    ),
    # Not in spec Set A → requires confirmation (see server/permissions.py).
    Tool("open_url", "system:open_url", "Open a URL in the browser", OpenUrlArgs, open_url),
]
