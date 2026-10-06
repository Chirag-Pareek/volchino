"""Stage 3: learned skills. Only ``status='approved'`` skills are ever matched or executed.

Trigger syntax: plain text matches the normalized request exactly; ``re:<pattern>`` is a
full-match regex whose named groups fill ``{placeholders}`` in step args.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiosqlite

from server.db import iso
from server.llm.opencode import SkillDraft
from server.pipeline.types import Plan, ToolCall

log = logging.getLogger(__name__)


def _fill(value: Any, params: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: params.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _fill(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, params) for v in value]
    return value


def _match_trigger(trigger: str, text: str) -> dict[str, str] | None:
    if trigger.startswith("re:"):
        try:
            m = re.fullmatch(trigger[3:], text)
        except re.error:
            log.warning("invalid skill regex %r", trigger)
            return None
        return {k: v for k, v in m.groupdict().items() if v is not None} if m else None
    return {} if trigger.strip().lower() == text else None


async def match(db: aiosqlite.Connection, text: str) -> Plan | None:
    async with db.execute(
        "SELECT name, trigger, steps, permissions FROM skills WHERE status = 'approved' "
        "ORDER BY success_rate DESC, name"
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        params = _match_trigger(row["trigger"], text)
        if params is None:
            continue
        try:
            steps = json.loads(row["steps"] or "[]")
            calls = [ToolCall(s["tool"], _fill(s.get("args", {}), params)) for s in steps]
        except (ValueError, KeyError, TypeError):
            log.warning("skill %s has malformed steps; skipping", row["name"])
            continue
        return Plan(
            calls=calls,
            stage="skill",
            intent=f"skill:{row['name']}",
            skill_name=row["name"],
            skill_requires_confirmation=row["permissions"] != "safe",
        )
    return None


async def save_draft(db: aiosqlite.Connection, draft: SkillDraft) -> str:
    """Persist an LLM-proposed skill. Always ``draft``; never overwrites an approved skill."""
    name = draft.name
    async with db.execute("SELECT status FROM skills WHERE name = ?", (name,)) as cur:
        existing = await cur.fetchone()
    if existing and existing["status"] == "approved":
        name = f"{name}_v2"
    await db.execute(
        """INSERT OR REPLACE INTO skills (name, description, trigger, parameters, required_tools,
           steps, permissions, status, success_criteria, success_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, 0.0)""",
        (
            name,
            draft.description,
            draft.trigger,
            json.dumps(draft.parameters),
            json.dumps(draft.required_tools),
            json.dumps(draft.steps),
            draft.permissions
            if draft.permissions in ("safe", "requires_confirmation")
            else "requires_confirmation",
            draft.success_criteria,
        ),
    )
    await db.commit()
    return name


async def approve(db: aiosqlite.Connection, name: str) -> bool:
    cur = await db.execute(
        "UPDATE skills SET status = 'approved' WHERE name = ? AND status = 'draft'", (name,)
    )
    await db.commit()
    return cur.rowcount > 0


async def record_result(db: aiosqlite.Connection, name: str, success: bool) -> None:
    # Exponential moving average keeps recent behaviour weighted.
    await db.execute(
        "UPDATE skills SET last_used = ?, success_rate = success_rate * 0.8 + ? WHERE name = ?",
        (iso(), 0.2 if success else 0.0, name),
    )
    await db.commit()
