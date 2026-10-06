"""Test router stage and multi-tier routing engine: Tier 1 to Tier 6."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from server.llm.groq import ChatClient, ChatResponse
from server.memory.preferences import get_preferences_context, set_preference
from server.pipeline import cache as cache_stage
from server.pipeline.cache import Cache
from server.pipeline.router import GroqRouter, parse_classification, parse_reply
from server.pipeline.types import Outcome
from server.reasoning.opencode import OpenCodeReasoningClient
from server.router.multi_tier import MultiTierRouter
from server.skills.engine import (
    approve_skill,
    match_approved_skill,
    save_draft_skill,
    synthesize_skill,
)
from server.tools import REGISTRY

# ── Reply Parsing Tests ──


def test_parse_valid():
    call = parse_reply('{"tool":"set_volume","args":{"level":30}}', REGISTRY)
    assert call is not None
    assert call.tool == "set_volume"
    assert call.args == {"level": 30}


def test_parse_needs_reasoning():
    call = parse_reply('{"tool":"needs_reasoning"}', REGISTRY)
    assert call is None


def test_parse_unknown_tool():
    call = parse_reply('{"tool":"nuke_everything","args":{}}', REGISTRY)
    assert call is None


def test_parse_invalid_json():
    call = parse_reply("not json at all", REGISTRY)
    assert call is None


def test_parse_bad_args():
    call = parse_reply('{"tool":"set_volume","args":{"level":999}}', REGISTRY)
    assert call is None


def test_parse_markdown_wrapper():
    call = parse_reply('```json\n{"tool":"set_volume","args":{"level":50}}\n```', REGISTRY)
    assert call is not None
    assert call.args == {"level": 50}


def test_parse_phase5_tool_schema():
    payload = '{"target":"tool","name":"set_volume","args":{"level":70},"confidence":0.98}'
    res = parse_classification(payload, REGISTRY)
    assert res is not None
    assert res.target == "tool"
    assert res.name == "set_volume"
    assert res.tool == "set_volume"
    assert res.args == {"level": 70}
    assert res.confidence == 0.98

    # parse_reply also returns it
    call = parse_reply(payload, REGISTRY)
    assert call is not None
    assert call.tool == "set_volume"


def test_parse_phase5_skill_schema():
    payload = '{"target":"skill","name":"dev_mode","args":{},"confidence":0.95}'
    # Without dev_mode approved
    res = parse_classification(payload, REGISTRY, approved_skills=[])
    assert res is not None
    assert res.target == "reasoning"

    # With dev_mode approved
    res = parse_classification(payload, REGISTRY, approved_skills=["dev_mode"])
    assert res is not None
    assert res.target == "skill"
    assert res.name == "dev_mode"


def test_parse_phase5_reasoning_schema():
    payload = '{"target":"reasoning","name":"","args":{},"confidence":0.3}'
    res = parse_classification(payload, REGISTRY)
    assert res is not None
    assert res.target == "reasoning"
    assert parse_reply(payload, REGISTRY) is None


async def test_route_no_client(db):
    cache = Cache(db)
    router = GroqRouter(None, cache, REGISTRY)
    assert router.enabled is False
    assert await router.route("anything") is None


# ── Tier 1: Zero-Token Local Cache Tests ──


async def test_tier1_cache_hit_returns_immediately_0_api_calls(db):
    cache = Cache(db)
    cached_outcome = Outcome(
        text="Cached volume set",
        stage="cache",
        intent="set_volume",
        tool="set_volume",
        args={"level": 25},
        tokens_used=0,
    )
    await cache_stage.store(cache, "volume 25%", cached_outcome, ttl_s=3600)

    # Mock client that would fail if called
    mock_client = MagicMock(spec=ChatClient)
    mock_client.complete = AsyncMock(side_effect=AssertionError("Groq API must not be called!"))
    router = GroqRouter(mock_client, cache, REGISTRY)
    reasoning = OpenCodeReasoningClient()

    multi_router = MultiTierRouter(db, cache, REGISTRY, router, reasoning)
    decision = await multi_router.decide("volume 25%", "volume 25%")

    assert decision.tier == 1
    assert decision.tier_name == "Local Cache"
    assert decision.target == "cache"
    assert decision.outcome is not None
    assert decision.outcome.text == "Cached volume set"
    assert decision.tokens_used == 0
    mock_client.complete.assert_not_called()


# ── Tier 4: Regex Match Bypasses Groq Tests ──


async def test_tier4_regex_match_bypasses_groq_0_api_calls(db):
    cache = Cache(db)
    mock_client = MagicMock(spec=ChatClient)
    mock_client.complete = AsyncMock(side_effect=AssertionError("Groq API must not be called!"))
    router = GroqRouter(mock_client, cache, REGISTRY)
    reasoning = OpenCodeReasoningClient()

    multi_router = MultiTierRouter(db, cache, REGISTRY, router, reasoning)

    # "workspace 2" matches deterministic regex
    decision = await multi_router.decide("workspace 2", "workspace 2")

    assert decision.tier == 4
    assert decision.tier_name == "Deterministic Regex"
    assert decision.target == "tool"
    assert decision.plan is not None
    assert decision.plan.calls[0].tool == "switch_workspace"
    assert decision.plan.calls[0].args == {"num": 2}
    assert decision.tokens_used == 0
    mock_client.complete.assert_not_called()


# ── Tier 5: Mock Groq Routing Tests ──


async def test_mock_groq_api_routing_to_tool(db):
    cache = Cache(db)
    mock_client = MagicMock(spec=ChatClient)
    # Return Phase 5 JSON schema
    mock_client.complete = AsyncMock(
        return_value=ChatResponse(
            content='{"target":"tool","name":"open_app","args":{"app":"code"},"confidence":0.99}',
            total_tokens=15,
        )
    )
    router = GroqRouter(mock_client, cache, REGISTRY)
    reasoning = OpenCodeReasoningClient()

    multi_router = MultiTierRouter(db, cache, REGISTRY, router, reasoning)
    decision = await multi_router.decide("launch visual studio", "launch visual studio")

    assert decision.tier == 5
    assert decision.tier_name == "Groq Router"
    assert decision.target == "tool"
    assert decision.plan is not None
    assert decision.plan.calls[0].tool == "open_app"
    assert decision.plan.calls[0].args == {"app": "code"}
    assert decision.tokens_used == 15
    mock_client.complete.assert_called_once()


async def test_mock_groq_api_routing_to_skill(db):
    cache = Cache(db)

    # Create and approve a skill
    skill_def = synthesize_skill(
        task_description="Setup developer mode",
        steps=[
            {"tool": "switch_workspace", "args": {"workspace": 1}},
            {"tool": "open_app", "args": {"app": "code"}},
        ],
        trigger="dev_mode",
        name="dev_mode",
    )
    name = await save_draft_skill(db, skill_def)
    await approve_skill(db, name)

    mock_client = MagicMock(spec=ChatClient)
    mock_client.complete = AsyncMock(
        return_value=ChatResponse(
            content='{"target":"skill","name":"dev_mode","args":{},"confidence":0.95}',
            total_tokens=18,
        )
    )
    router = GroqRouter(mock_client, cache, REGISTRY)
    reasoning = OpenCodeReasoningClient()

    multi_router = MultiTierRouter(db, cache, REGISTRY, router, reasoning)
    decision = await multi_router.decide("time to code", "time to code")

    assert decision.tier == 5
    assert decision.target == "skill"
    assert decision.plan is not None
    assert decision.plan.skill_name == "dev_mode"
    assert len(decision.plan.calls) == 2
    assert decision.tokens_used == 18


# ── Tier 3 & Self-Learning Skills Tests ──


async def test_draft_skill_cannot_execute_until_approved(db):
    """Verify draft skills are never matched or executed at Tier 3."""
    skill_def = synthesize_skill(
        task_description="Secret admin routine",
        steps=[{"tool": "set_volume", "args": {"level": 80}}],
        trigger="super secret trigger",
        name="secret_routine",
    )
    name = await save_draft_skill(db, skill_def)
    assert name == "secret_routine"

    # Attempt Tier 3 match on the draft skill
    matched = await match_approved_skill(db, "super secret trigger")
    assert matched is None, "Draft skills must NEVER match before approval!"


async def test_approve_skill_enables_execution_at_0_tokens(db):
    """Verify approval transitions status to approved, enabling Tier 3 execution at 0 tokens."""
    skill_def = synthesize_skill(
        task_description="Mute sound",
        steps=[{"tool": "set_volume", "args": {"level": 0}}],
        trigger="mute everything",
        name="mute_all",
    )
    await save_draft_skill(db, skill_def)

    # Before approval: no match
    assert await match_approved_skill(db, "mute everything") is None

    # Approve
    ok = await approve_skill(db, "mute_all")
    assert ok is True

    # After approval: matches cleanly at Tier 3 with 0 tokens
    matched = await match_approved_skill(db, "mute everything")
    assert matched is not None
    assert matched.skill_name == "mute_all"
    assert matched.calls[0].tool == "set_volume"
    assert matched.calls[0].args == {"level": 0}
    assert matched.tokens_used == 0


# ── Preferences & Context Engine Tests ──


async def test_preferences_context_injection(db):
    await set_preference(db, category="app", rule="editor", instruction="code")
    await set_preference(db, category="workspace", rule="work", instruction="3")
    await set_preference(
        db, category="rules", rule="silent_mode", instruction="Never speak aloud past 10pm"
    )

    context = await get_preferences_context(db)
    assert context["app_preferences"]["editor"] == "code"
    assert context["workspace_preferences"]["work"] == 3
    assert any("10pm" in r for r in context["rules"])
