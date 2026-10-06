"""Stage 5 (Phase 5): Groq router re-export for pipeline integration.

Maintains backward-compatibility while forwarding to server.router.router_groq.
"""

from __future__ import annotations

from server.pipeline.types import ToolCall
from server.router.router_groq import (
    NEEDS_REASONING,
    NEEDS_REASONING_TTL_S,
    ROUTE_TTL_S,
    GroqRouter,
    RouteClassification,
    RouteResult,
    build_system_prompt,
    parse_classification,
)
from server.router.router_groq import (
    parse_reply as _parse_reply_groq,
)
from server.tools.base import Tool


def parse_reply(content: str, registry: dict[str, Tool]) -> ToolCall | None:
    """Parse reply into a ToolCall for backwards compatibility."""
    res = _parse_reply_groq(content, registry)
    if res is None or res.target != "tool":
        return None
    return ToolCall(res.name, res.args)


__all__ = [
    "NEEDS_REASONING",
    "NEEDS_REASONING_TTL_S",
    "ROUTE_TTL_S",
    "GroqRouter",
    "RouteClassification",
    "RouteResult",
    "build_system_prompt",
    "parse_classification",
    "parse_reply",
]
