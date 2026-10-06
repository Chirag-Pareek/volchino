"""Stage 3: learned skills. Only ``status='approved'`` skills are ever matched or executed.

Delegates core skill logic to server.skills.engine.
"""

from __future__ import annotations

import aiosqlite

from server.llm.opencode import SkillDraft
from server.skills.engine import (
    approve_skill as approve,
)
from server.skills.engine import (
    match_approved_skill as match,
)
from server.skills.engine import (
    record_skill_result as record_result,
)
from server.skills.engine import (
    save_draft_skill,
)


async def save_draft(db: aiosqlite.Connection, draft: SkillDraft) -> str:
    """Persist an LLM-proposed skill draft. Always draft; never auto-approved."""
    skill_def = {
        "name": draft.name,
        "description": draft.description,
        "trigger": draft.trigger,
        "parameters": draft.parameters,
        "required_tools": draft.required_tools,
        "steps": draft.steps,
        "permissions": draft.permissions,
        "success_criteria": draft.success_criteria,
    }
    return await save_draft_skill(db, skill_def)


__all__ = ["approve", "match", "record_result", "save_draft"]
