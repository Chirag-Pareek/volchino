"""Tier 5: Ultra-fast Groq Router.

Classifies user prompts using strict JSON schema:
    {
        "target": "tool" | "skill" | "reasoning",
        "name": "<tool_or_skill_name_or_empty>",
        "args": { ... },
        "confidence": 0.0 - 1.0
    }

Latency target: <300ms.
Classifications are cached in SQLite `cache` under `route:<text>` for 24h.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from server.llm.groq import ChatClient, LLMError
from server.pipeline.cache import Cache, route_key
from server.pipeline.types import ToolCall
from server.tools.base import Tool, ToolArgError

log = logging.getLogger(__name__)

TargetKind = Literal["tool", "skill", "reasoning"]
NEEDS_REASONING = "needs_reasoning"
ROUTE_TTL_S = 24 * 3600
NEEDS_REASONING_TTL_S = 3600


@dataclass
class RouteClassification:
    target: TargetKind
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    @property
    def tool(self) -> str:
        """Alias for name for compatibility with ToolCall consumers."""
        return self.name


@dataclass
class RouteResult:
    call: ToolCall | None = None  # None => needs reasoning
    skill_name: str | None = None  # Non-empty if target was a learned skill
    classification: RouteClassification | None = None
    tokens_used: int = 0
    cached: bool = False


def build_system_prompt(
    registry: dict[str, Tool],
    approved_skills: list[str] | None = None,
    preferences: dict[str, Any] | None = None,
) -> str:
    """Construct ultra-fast router system prompt enforcing the strict JSON schema."""
    tools_catalog = [
        {"name": t.name, "description": t.description, "args": t.schema()}
        for t in registry.values()
    ]
    skills_list = approved_skills or []
    pref_rules = (preferences or {}).get("rules", [])

    return (
        "You are an ultra-fast intent router for the Volchino Linux desktop assistant.\n"
        "Your task: classify the user's prompt into exactly one target.\n"
        f"AVAILABLE TOOLS: {json.dumps(tools_catalog, separators=(',', ':'))}\n"
        f"APPROVED SKILLS: {json.dumps(skills_list, separators=(',', ':'))}\n"
        f"USER PREFERENCES: {json.dumps(pref_rules, separators=(',', ':'))}\n"
        "You MUST respond with a valid JSON object strictly matching this schema:\n"
        "{\n"
        '  "target": "tool" | "skill" | "reasoning",\n'
        '  "name": "<tool_name_or_skill_name>",\n'
        '  "args": { ... },\n'
        '  "confidence": 0.0 to 1.0\n'
        "}\n"
        "Rules:\n"
        '1. If a single tool fits, set "target": "tool", "name": "<tool>", and extract its args.\n'
        '2. If an approved skill matches, set "target": "skill", "name": "<skill>", "args": {}.\n'
        "3. If no single tool or skill fits, or it requires complex multi-step reasoning, set "
        '"target": "reasoning", "name": "", "args": {}.\n'
        "4. NEVER invent tools or arguments outside the catalog.\n"
        "5. Respond with raw JSON only. No explanations, no markdown fences."
    )


def parse_classification(
    content: str,
    registry: dict[str, Tool],
    approved_skills: list[str] | None = None,
) -> RouteClassification | None:
    """Parse model JSON reply into a full RouteClassification."""
    clean = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(clean)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # Check for Phase 5 schema: target, name, args, confidence
    if "target" in data:
        target = data.get("target")
        name = str(data.get("name") or "").strip()
        args = data.get("args") or {}
        confidence = float(data.get("confidence", 1.0))

        if target == "tool":
            if name not in registry or not isinstance(args, dict):
                return RouteClassification(target="reasoning", confidence=0.0)
            try:
                registry[name].validate(args)
            except ToolArgError:
                return RouteClassification(target="reasoning", confidence=0.0)
            return RouteClassification(target="tool", name=name, args=args, confidence=confidence)

        if target == "skill":
            skills = approved_skills or []
            if name in skills:
                return RouteClassification(
                    target="skill", name=name, args=args, confidence=confidence
                )
            return RouteClassification(target="reasoning", confidence=0.0)

        return RouteClassification(target="reasoning", confidence=confidence)

    # Backwards-compatible schema: {"tool": "<name>", "args": {...}}
    name = data.get("tool")
    args = data.get("args") or {}
    if name == NEEDS_REASONING or not name or name not in registry or not isinstance(args, dict):
        return RouteClassification(target="reasoning", confidence=0.0)

    try:
        registry[name].validate(args)
    except ToolArgError:
        return RouteClassification(target="reasoning", confidence=0.0)

    return RouteClassification(target="tool", name=name, args=args, confidence=1.0)


def parse_reply(
    content: str,
    registry: dict[str, Tool],
    approved_skills: list[str] | None = None,
) -> RouteClassification | None:
    """Parse reply into a RouteClassification; returns None if target is reasoning or invalid."""
    res = parse_classification(content, registry, approved_skills)
    if res is None or res.target == "reasoning":
        return None
    return res


class GroqRouter:
    """Tier 5 ultra-fast Groq intent router."""

    def __init__(
        self,
        client: ChatClient | None,
        cache: Cache,
        registry: dict[str, Tool],
        approved_skills_provider: Any = None,
        preferences_provider: Any = None,
    ):
        self.client = client
        self.cache = cache
        self.registry = registry
        self.approved_skills_provider = approved_skills_provider
        self.preferences_provider = preferences_provider

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def route(
        self,
        text: str,
        *,
        approved_skills: list[str] | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> RouteResult | None:
        """Route request through cached classification or Groq API.

        Returns RouteResult, or None if router is unavailable (disabled / network error).
        """
        cached = await self.cache.get(route_key(text))
        if cached is not None:
            target = cached.get("target")
            if target == "tool":
                call = ToolCall(cached["name"], cached.get("args", {}))
                return RouteResult(call=call, cached=True)
            if target == "skill":
                return RouteResult(skill_name=cached.get("name"), cached=True)
            # Legacy cache check
            if "tool" in cached:
                if cached["tool"] == NEEDS_REASONING:
                    return RouteResult(call=None, cached=True)
                return RouteResult(ToolCall(cached["tool"], cached.get("args", {})), cached=True)
            return RouteResult(call=None, cached=True)

        if self.client is None:
            return None

        # Gather dynamic skills and preferences if providers are present
        skills_list = approved_skills or []
        if not skills_list and callable(self.approved_skills_provider):
            skills_list = await self.approved_skills_provider()

        prefs = preferences or {}
        if not prefs and callable(self.preferences_provider):
            prefs = await self.preferences_provider()

        system_prompt = build_system_prompt(self.registry, skills_list, prefs)

        try:
            resp = await self.client.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ]
            )
        except LLMError as e:
            log.warning("Groq router unavailable: %s", e)
            return None

        classification = parse_reply(resp.content, self.registry, skills_list)
        if classification is None or classification.target == "reasoning":
            await self.cache.set(
                route_key(text),
                {"target": "reasoning"},
                NEEDS_REASONING_TTL_S,
            )
            return RouteResult(
                call=None,
                classification=classification,
                tokens_used=resp.total_tokens,
            )

        if classification.target == "tool":
            call = ToolCall(classification.name, classification.args)
            await self.cache.set(
                route_key(text),
                {"target": "tool", "name": call.tool, "args": call.args},
                ROUTE_TTL_S,
            )
            return RouteResult(
                call=call,
                classification=classification,
                tokens_used=resp.total_tokens,
            )

        if classification.target == "skill":
            await self.cache.set(
                route_key(text),
                {"target": "skill", "name": classification.name},
                ROUTE_TTL_S,
            )
            return RouteResult(
                call=None,
                skill_name=classification.name,
                classification=classification,
                tokens_used=resp.total_tokens,
            )

        return RouteResult(call=None, tokens_used=resp.total_tokens)
