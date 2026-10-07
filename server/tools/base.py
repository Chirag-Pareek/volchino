"""Tool primitives: subprocess runner (no shell, timeouts), Tool definition, errors."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import aiosqlite
from pydantic import BaseModel, ConfigDict, ValidationError

from server.config import Settings

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """A tool failed while executing (missing binary, non-zero exit, timeout...)."""


class ToolArgError(ValueError):
    """Arguments for a tool were invalid."""


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    async def run(
        self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CmdResult: ...

    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    """Runs argv lists via ``create_subprocess_exec`` (never a shell) with a hard timeout."""

    async def run(
        self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CmdResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ToolArgError("argv must be a non-empty list of strings")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(env) if env is not None else None,
            )
        except FileNotFoundError as e:
            raise ToolError(f"{argv[0]} is not installed") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise ToolError(f"{argv[0]} timed out after {timeout:.0f}s") from e
        return CmdResult(
            proc.returncode or 0,
            out.decode(errors="replace").strip(),
            err.decode(errors="replace").strip(),
        )

    def which(self, name: str) -> str | None:
        return shutil.which(name)


@dataclass
class DryRunRunner:
    """Records argv instead of executing. Used by tests and ``VOLCHINO_DRY_RUN=1``."""

    calls: list[list[str]] = field(default_factory=list)
    result: CmdResult = field(default_factory=lambda: CmdResult(0, "", ""))
    installed: set[str] | None = None  # None = pretend everything is installed

    async def run(
        self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CmdResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ToolArgError("argv must be a non-empty list of strings")
        self.calls.append(list(argv))
        log.info("[dry-run] %s", argv)
        return self.result

    def which(self, name: str) -> str | None:
        if self.installed is None or name in self.installed:
            return f"/usr/bin/{name}"
        return None


Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ToolContext:
    settings: Settings
    runner: Runner
    db: aiosqlite.Connection
    broadcast: Broadcast | None = None
    adb: Any | None = None
    image_gen: Any | None = None


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


ToolFunc = Callable[[Any, ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    permission: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc
    cache_ttl_s: int | None = None  # only read-only tools may be cached

    def validate(self, args: Mapping[str, Any] | None) -> BaseModel:
        try:
            return self.args_model.model_validate(dict(args or {}))
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'args'}: {err['msg']}" for err in e.errors()
            )
            raise ToolArgError(f"{self.name}: {msgs}") from e

    async def __call__(self, args: Mapping[str, Any] | None, ctx: ToolContext) -> str:
        return await self.func(self.validate(args), ctx)

    def schema(self) -> dict[str, Any]:
        props = self.args_model.model_json_schema().get("properties", {})
        return {
            name: {
                k: v
                for k, v in spec.items()
                if k in ("type", "description", "minimum", "maximum", "default")
            }
            for name, spec in props.items()
        }


async def run_checked(
    ctx: ToolContext, argv: list[str], *, env: Mapping[str, str] | None = None
) -> CmdResult:
    if ctx.runner.which(argv[0]) is None:
        raise ToolError(f"{argv[0]} is not installed")
    res = await ctx.runner.run(argv, timeout=ctx.settings.tool_timeout_s, env=env)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").splitlines()[:1]
        raise ToolError(f"{argv[0]} exited {res.returncode}: {detail[0] if detail else ''}")
    return res
