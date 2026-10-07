"""Tool registry: the 10 deterministic tools from spec §4."""

from __future__ import annotations

from server.tools import android, hyprland, memory_tools, system
from server.tools.base import (
    CmdResult,
    DryRunRunner,
    Runner,
    SubprocessRunner,
    Tool,
    ToolArgError,
    ToolContext,
    ToolError,
)

REGISTRY: dict[str, Tool] = {
    t.name: t for t in (*hyprland.TOOLS, *system.TOOLS, *memory_tools.TOOLS, *android.TOOLS)
}

__all__ = [
    "REGISTRY",
    "CmdResult",
    "DryRunRunner",
    "Runner",
    "SubprocessRunner",
    "Tool",
    "ToolArgError",
    "ToolContext",
    "ToolError",
]
