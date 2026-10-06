"""Self-Learning Skill Engine.

Synthesizes multi-step routines into persistent skill definitions according to spec §9.2.
New skills are ALWAYS saved with status='draft' and CANNOT be executed until explicitly approved.
Approved skills execute at 0 tokens via Tier 3 matching.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiosqlite

from server.db import iso
from server.pipeline.types import Plan, ToolCall

log = logging.getLogger(__name__)


def slugify(text: str, max_len: int = 40) -> str:
    """Turn human text into a clean snake_case identifier."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len].rstrip("_") or "skill"


def _fill_placeholders(value: Any, params: dict[str, str]) -> Any:
    """Substitute {var} templates inside step arguments."""
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: params.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _fill_placeholders(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_placeholders(v, params) for v in value]
    return value


def _match_trigger(trigger: str, text: str) -> dict[str, str] | None:
    """Match request text against a skill trigger (plain text or re:<regex>)."""
    if trigger.startswith("re:"):
        try:
            m = re.fullmatch(trigger[3:], text)
        except re.error:
            log.warning("invalid regex in skill trigger: %r", trigger)
            return None
        return {k: v for k, v in m.groupdict().items() if v is not None} if m else None
    return {} if trigger.strip().lower() == text else None


def synthesize_skill(
    task_description: str,
    steps: list[dict[str, Any]],
    trigger: str,
    *,
    name: str | None = None,
    parameters: dict[str, Any] | None = None,
    permissions: str = "requires_confirmation",
    success_criteria: str = "",
) -> dict[str, Any]:
    """Synthesize a complete skill definition JSON matching spec §9.2."""
    skill_name = name or slugify(task_description or trigger)
    required_tools = sorted({s["tool"] for s in steps if isinstance(s, dict) and "tool" in s})
    return {
        "name": skill_name,
        "description": task_description,
        "trigger": trigger,
        "parameters": parameters or {},
        "required_tools": required_tools,
        "steps": steps,
        "permissions": permissions
        if permissions in ("safe", "requires_confirmation")
        else "requires_confirmation",
        "status": "draft",  # ALWAYS draft initially
        "success_criteria": success_criteria or "All steps completed successfully",
        "success_rate": 0.0,
    }


async def save_draft_skill(db: aiosqlite.Connection, skill_def: dict[str, Any]) -> str:
    """Insert skill into SQLite `skills` table with status='draft'.

    NEVER overwrites an already-approved skill; if the name collides, appends _v2, _v3, etc.
    Returns the final assigned skill name.
    """
    base_name = skill_def.get("name", "custom_skill")
    name = base_name

    # Check for collisions with existing approved skills
    async with db.execute("SELECT status FROM skills WHERE name = ?", (name,)) as cur:
        existing = await cur.fetchone()

    if existing and existing["status"] == "approved":
        version = 2
        while True:
            candidate = f"{base_name}_v{version}"
            async with db.execute("SELECT status FROM skills WHERE name = ?", (candidate,)) as cur:
                collision = await cur.fetchone()
            if not collision or collision["status"] == "draft":
                name = candidate
                break
            version += 1

    steps = skill_def.get("steps") or []
    required_tools = skill_def.get("required_tools") or sorted(
        {s["tool"] for s in steps if isinstance(s, dict) and "tool" in s}
    )

    await db.execute(
        """INSERT OR REPLACE INTO skills (
               name, description, trigger, parameters, required_tools,
               steps, permissions, status, success_criteria, success_rate, last_used
           ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, 0.0, NULL)""",
        (
            name,
            skill_def.get("description", ""),
            skill_def.get("trigger", name),
            json.dumps(skill_def.get("parameters", {})),
            json.dumps(required_tools),
            json.dumps(steps),
            skill_def.get("permissions", "requires_confirmation"),
            skill_def.get("success_criteria", ""),
        ),
    )
    await db.commit()
    return name


async def approve_skill(db: aiosqlite.Connection, name: str) -> bool:
    """Approve a draft skill so it can be executed at 0 tokens."""
    cur = await db.execute(
        "UPDATE skills SET status = 'approved' WHERE name = ? AND status = 'draft'",
        (name,),
    )
    await db.commit()
    return cur.rowcount > 0


async def get_skill(db: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    """Fetch a skill definition by name."""
    async with db.execute("SELECT * FROM skills WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    r["steps"] = json.loads(r.get("steps") or "[]")
    r["required_tools"] = json.loads(r.get("required_tools") or "[]")
    r["parameters"] = json.loads(r.get("parameters") or "{}")
    return r


async def list_skills(db: aiosqlite.Connection, status: str | None = None) -> list[dict[str, Any]]:
    """List skills, optionally filtered by status ('draft' | 'approved')."""
    if status:
        sql = "SELECT * FROM skills WHERE status = ? ORDER BY name"
        params = (status,)
    else:
        sql = "SELECT * FROM skills ORDER BY status, name"
        params = ()

    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        r["steps"] = json.loads(r.get("steps") or "[]")
        r["required_tools"] = json.loads(r.get("required_tools") or "[]")
        r["parameters"] = json.loads(r.get("parameters") or "{}")
        results.append(r)
    return results


async def match_approved_skill(db: aiosqlite.Connection, text: str) -> Plan | None:
    """Find an approved skill whose trigger matches the user request.

    Returns an executable Plan, or None if no approved skill matches.
    Draft skills are NEVER returned here.
    """
    async with db.execute(
        """SELECT name, trigger, steps, permissions FROM skills
           WHERE status = 'approved'
           ORDER BY success_rate DESC, name"""
    ) as cur:
        rows = await cur.fetchall()

    for row in rows:
        params = _match_trigger(row["trigger"], text)
        if params is None:
            continue
        try:
            steps = json.loads(row["steps"] or "[]")
            calls = [
                ToolCall(s["tool"], _fill_placeholders(s.get("args", {}), params))
                for s in steps
                if isinstance(s, dict) and "tool" in s
            ]
        except (ValueError, KeyError, TypeError):
            log.warning("Skill %s has malformed steps; skipping", row["name"])
            continue

        if not calls:
            continue

        return Plan(
            calls=calls,
            stage="skill",
            intent=f"skill:{row['name']}",
            skill_name=row["name"],
            skill_requires_confirmation=row["permissions"] != "safe",
        )
    return None


async def record_skill_result(db: aiosqlite.Connection, name: str, success: bool) -> None:
    """Update success rate and last_used timestamp for a skill."""
    async with db.execute("SELECT success_rate FROM skills WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
    if not row:
        return
    prev = float(row["success_rate"] or 0.0)
    # Exponential moving average
    alpha = 0.2
    new_rate = (1.0 - alpha) * prev + alpha * (1.0 if success else 0.0)
    await db.execute(
        "UPDATE skills SET success_rate = ?, last_used = ? WHERE name = ?",
        (round(new_rate, 3), iso(), name),
    )
    await db.commit()
