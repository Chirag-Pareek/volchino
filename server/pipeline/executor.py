"""Executes plans: validates args, applies the permission gate, runs tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from server.permissions import Decision, check_all
from server.pipeline.types import Plan
from server.tools.base import Tool, ToolArgError, ToolContext, ToolError

log = logging.getLogger(__name__)


@dataclass
class ExecResult:
    text: str
    ok: bool
    tool: str | None


class Executor:
    def __init__(self, registry: dict[str, Tool], ctx: ToolContext) -> None:
        self.registry = registry
        self.ctx = ctx

    def validate(self, plan: Plan) -> str | None:
        """Return an error message if any call is unknown or has invalid args."""
        if not plan.calls:
            return "That skill has no steps yet."
        for call in plan.calls:
            tool = self.registry.get(call.tool)
            if tool is None:
                return f"Unknown tool {call.tool!r}."
            try:
                tool.validate(call.args)
            except ToolArgError as e:
                return f"Invalid arguments: {e}"
        return None

    def permissions(self, plan: Plan) -> list[str]:
        perms = [self.registry[c.tool].permission for c in plan.calls if c.tool in self.registry]
        if plan.skill_requires_confirmation:
            perms.append("skill:requires_confirmation")
        return perms

    def gate(self, plan: Plan) -> Decision:
        return check_all(self.permissions(plan))

    def describe(self, plan: Plan) -> str:
        steps = ", ".join(
            f"{c.tool}({', '.join(f'{k}={v!r}' for k, v in c.args.items())})" for c in plan.calls
        )
        prefix = f"Run skill '{plan.skill_name}': " if plan.skill_name else "Run "
        return prefix + steps

    async def run(self, plan: Plan) -> ExecResult:
        """Execute every call in order. Callers MUST have checked :meth:`gate` first."""
        texts: list[str] = []
        last_tool: str | None = None
        for call in plan.calls:
            tool = self.registry.get(call.tool)
            if tool is None:
                return ExecResult(f"Unknown tool {call.tool!r}.", False, call.tool)
            last_tool = tool.name
            try:
                texts.append(await tool(call.args, self.ctx))
            except ToolArgError as e:
                return ExecResult(f"Invalid arguments: {e}", False, tool.name)
            except ToolError as e:
                return ExecResult(f"{tool.name} failed: {e}", False, tool.name)
            except Exception:
                log.exception("tool %s crashed", tool.name)
                return ExecResult(f"{tool.name} failed unexpectedly.", False, tool.name)
        return ExecResult(" ".join(texts), True, last_tool)
