"""Multi-Tier Router Engine: Orchestrates Tiers 1 through 6.

Enforces zero-token prioritization:
    Tier 1: Zero-Token Local Cache (exact match / normalization / TTL expiration)
    Tier 2: SQLite Memory & Stats lookup (direct SQL queries for pet, activity, preferences)
    Tier 3: Learned Executable Skills lookup (approved skills only)
    Tier 4: Deterministic Regex / Alias tool matcher (Hyprland, Linux, system tools)
    Tier 5: Ultra-Fast Groq Router (<300ms, strict JSON schema classification)
    Tier 6: OpenCode Go Deep Reasoning Client (complex multi-step formulation)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

from server.memory.preferences import get_preferences_context
from server.pipeline import cache as cache_stage
from server.pipeline import deterministic, memory
from server.pipeline.cache import Cache
from server.pipeline.types import Outcome, Plan, ToolCall
from server.reasoning.opencode import OpenCodeReasoningClient, ReasoningResult
from server.router.router_groq import GroqRouter
from server.skills.engine import get_skill, list_skills, match_approved_skill
from server.tools.base import Tool

log = logging.getLogger(__name__)

# Tools that match on free text and must be validated before skipping Tier 5
_FUZZY_TOOLS = {"open_app", "focus_window", "open_url"}


@dataclass
class TierDecision:
    """The decision produced by the multi-tier routing engine."""

    tier: int
    tier_name: str
    target: str  # "cache", "memory", "skill", "tool", "reasoning"
    outcome: Outcome | None = None
    plan: Plan | None = None
    reasoning: ReasoningResult | None = None
    tokens_used: int = 0


class MultiTierRouter:
    """6-Tier Decision Engine coordinating all stages of request resolution."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        cache: Cache,
        registry: dict[str, Tool],
        groq_router: GroqRouter,
        reasoning_client: OpenCodeReasoningClient,
    ):
        self.db = db
        self.cache = cache
        self.registry = registry
        self.groq_router = groq_router
        self.reasoning_client = reasoning_client

    async def decide(self, raw_input: str, text: str) -> TierDecision:
        """Evaluate request through Tiers 1 to 6 in priority order."""
        # ── Tier 1: Zero-Token Local Cache ──
        hit = await cache_stage.lookup(self.cache, text)
        if hit is not None:
            return TierDecision(
                tier=1,
                tier_name="Local Cache",
                target="cache",
                outcome=hit,
                tokens_used=0,
            )

        # ── Tier 2: SQLite Memory & Stats Lookup ──
        mem_outcome = await memory.handle(self.db, text)
        if mem_outcome is not None:
            return TierDecision(
                tier=2,
                tier_name="SQLite Memory",
                target="memory",
                outcome=mem_outcome,
                tokens_used=0,
            )

        # ── Tier 3: Learned Executable Skills Lookup ──
        skill_plan = await match_approved_skill(self.db, text)
        if skill_plan is not None:
            return TierDecision(
                tier=3,
                tier_name="Learned Skills",
                target="skill",
                plan=skill_plan,
                tokens_used=0,
            )

        # ── Tier 4: Deterministic Regex / Alias Tool Matcher ──
        det_call = deterministic.match(text)
        if det_call is not None:
            # Check if valid args
            valid = True
            if det_call.tool in self.registry:
                try:
                    self.registry[det_call.tool].validate(det_call.args)
                except Exception:
                    if det_call.tool in _FUZZY_TOOLS:
                        valid = False

            if valid:
                plan = Plan([det_call], stage="tool", intent=det_call.tool, tokens_used=0)
                return TierDecision(
                    tier=4,
                    tier_name="Deterministic Regex",
                    target="tool",
                    plan=plan,
                    tokens_used=0,
                )

        # Gather preferences and approved skills for LLM tiers
        preferences = await get_preferences_context(self.db)
        approved_skills_rows = await list_skills(self.db, status="approved")
        approved_names = [s["name"] for s in approved_skills_rows]

        # ── Tier 5: Ultra-Fast Groq Router ──
        tokens_so_far = 0
        if self.groq_router.enabled:
            route_res = await self.groq_router.route(
                text,
                approved_skills=approved_names,
                preferences=preferences,
            )
            if route_res is not None:
                tokens_so_far = route_res.tokens_used
                if route_res.call is not None:
                    plan = Plan(
                        [route_res.call],
                        stage="router",
                        intent=route_res.call.tool,
                        tokens_used=tokens_so_far,
                    )
                    return TierDecision(
                        tier=5,
                        tier_name="Groq Router",
                        target="tool",
                        plan=plan,
                        tokens_used=tokens_so_far,
                    )
                if route_res.skill_name:
                    # Target was an approved skill
                    skill_row = await get_skill(self.db, route_res.skill_name)
                    if skill_row and skill_row.get("status") == "approved":
                        steps = skill_row.get("steps", [])
                        calls = [
                            ToolCall(s["tool"], s.get("args", {}))
                            for s in steps
                            if isinstance(s, dict) and "tool" in s
                        ]
                        if calls:
                            plan = Plan(
                                calls=calls,
                                stage="skill",
                                intent=f"skill:{route_res.skill_name}",
                                skill_name=route_res.skill_name,
                                tokens_used=tokens_so_far,
                                skill_requires_confirmation=skill_row.get("permissions") != "safe",
                            )
                            return TierDecision(
                                tier=5,
                                tier_name="Groq Router",
                                target="skill",
                                plan=plan,
                                tokens_used=tokens_so_far,
                            )

        # ── Tier 6: OpenCode Go Deep Reasoning ──
        catalog = {
            name: {"description": t.description, "args": t.schema()}
            for name, t in self.registry.items()
        }
        reasoning_res = await self.reasoning_client.reason(
            text,
            tool_catalog=catalog,
            preferences=preferences,
        )
        tokens_so_far += reasoning_res.tokens_used

        return TierDecision(
            tier=6,
            tier_name="OpenCode Reasoning",
            target="reasoning",
            reasoning=reasoning_res,
            tokens_used=tokens_so_far,
        )
