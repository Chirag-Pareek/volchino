"""Stage 6: heavy-reasoning fallback. Produces a DRAFT skill that needs user approval."""

from __future__ import annotations

import aiosqlite

from server.llm.opencode import SkillProposer
from server.pipeline import skills
from server.pipeline.types import Outcome, PendingAction
from server.tools.base import Tool


async def handle(
    db: aiosqlite.Connection,
    proposer: SkillProposer,
    registry: dict[str, Tool],
    *,
    user_input: str,
    text: str,
    tokens_so_far: int = 0,
) -> Outcome:
    catalog = {
        name: {"description": t.description, "args": t.schema()} for name, t in registry.items()
    }
    draft = await proposer.propose_skill(text, catalog)
    # Drop any step that references an unknown tool; the draft still never runs unapproved.
    draft.steps = [s for s in draft.steps if isinstance(s, dict) and s.get("tool") in registry]
    draft.required_tools = sorted({s["tool"] for s in draft.steps})
    name = await skills.save_draft(db, draft)

    proposal = {
        "name": name,
        "description": draft.description,
        "trigger": draft.trigger,
        "steps": draft.steps,
        "required_tools": draft.required_tools,
    }

    pending = PendingAction(
        kind="approve_skill",
        action=f"Approve draft skill '{name}'? ({len(draft.steps)} step(s): "
        f"{', '.join(draft.required_tools) or 'none yet'})",
        user_input=user_input,
        text=text,
        skill_name=name,
        skill_proposal=proposal,
    )
    return Outcome(
        text=f"I don't know how to do that yet. I saved a draft skill '{name}' -- it won't run "
        "until you approve it.",
        stage="fallback",
        intent="needs_reasoning",
        tool=None,
        tokens_used=tokens_so_far + draft.tokens_used,
        status="pending_confirmation",
        pending=pending,
        skill_proposal=proposal,
    )
