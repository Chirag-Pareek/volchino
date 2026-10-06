"""Stage 5 (Phase 5 skeleton): Groq cheap router.

Classifies a request into ``{"tool": <name>, "args": {...}}`` or ``{"tool": "needs_reasoning"}``.
Classifications are cached under ``route:<text>`` so repeated phrases cost 0 tokens.
The router can only pick registered tools; the permission gate still applies afterwards.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from server.llm.groq import ChatClient, LLMError
from server.pipeline.cache import Cache, route_key
from server.pipeline.types import ToolCall
from server.tools.base import Tool, ToolArgError

log = logging.getLogger(__name__)

NEEDS_REASONING = "needs_reasoning"
ROUTE_TTL_S = 24 * 3600
NEEDS_REASONING_TTL_S = 3600


@dataclass
class RouteResult:
    call: ToolCall | None  # None => needs reasoning
    tokens_used: int = 0
    cached: bool = False


def build_system_prompt(registry: dict[str, Tool]) -> str:
    catalog = [
        {"tool": t.name, "description": t.description, "args": t.schema()}
        for t in registry.values()
    ]
    return (
        "You are an intent router for a Linux desktop assistant. Map the user's request to "
        "exactly one tool from this catalog and extract its arguments.\n"
        f"CATALOG: {json.dumps(catalog, separators=(',', ':'))}\n"
        'Reply with JSON only: {"tool": "<tool name>", "args": {...}}. '
        f'If no single tool fits, or it needs multi-step reasoning, reply {{"tool": '
        f'"{NEEDS_REASONING}"}}. Never invent tools or arguments.'
    )


def parse_reply(content: str, registry: dict[str, Tool]) -> ToolCall | None:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("tool")
    args = data.get("args") or {}
    if name == NEEDS_REASONING or name not in registry or not isinstance(args, dict):
        return None
    try:
        registry[name].validate(args)
    except ToolArgError:
        return None
    return ToolCall(name, args)


class GroqRouter:
    def __init__(self, client: ChatClient | None, cache: Cache, registry: dict[str, Tool]):
        self.client = client
        self.cache = cache
        self.registry = registry
        self._system = build_system_prompt(registry)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def route(self, text: str) -> RouteResult | None:
        """Return a RouteResult, or None if the router is unavailable (disabled / error)."""
        cached = await self.cache.get(route_key(text))
        if cached is not None:
            if cached.get("tool") == NEEDS_REASONING:
                return RouteResult(None, cached=True)
            return RouteResult(ToolCall(cached["tool"], cached.get("args", {})), cached=True)
        if self.client is None:
            return None
        try:
            resp = await self.client.complete(
                [{"role": "system", "content": self._system}, {"role": "user", "content": text}]
            )
        except LLMError as e:
            log.warning("router unavailable: %s", e)
            return None
        call = parse_reply(resp.content, self.registry)
        if call is None:
            await self.cache.set(route_key(text), {"tool": NEEDS_REASONING}, NEEDS_REASONING_TTL_S)
        else:
            await self.cache.set(
                route_key(text), {"tool": call.tool, "args": call.args}, ROUTE_TTL_S
            )
        return RouteResult(call, tokens_used=resp.total_tokens)
