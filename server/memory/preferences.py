"""Preferences and context engine: queries and updates user preferences from SQLite.

Used to inject preferences into the Groq router and OpenCode reasoning contexts
(e.g., favorite apps, default workspaces, custom rules).
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from server.db import iso

log = logging.getLogger(__name__)


async def get_all_preferences(
    db: aiosqlite.Connection, category: str | None = None
) -> list[dict[str, Any]]:
    """Retrieve all preferences, optionally filtered by category."""
    if category:
        sql = (
            "SELECT category, rule, instruction, confidence, last_updated "
            "FROM preferences WHERE category = ? ORDER BY last_updated DESC"
        )
        params = (category,)
    else:
        sql = (
            "SELECT category, rule, instruction, confidence, last_updated "
            "FROM preferences ORDER BY category, last_updated DESC"
        )
        params = ()

    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def set_preference(
    db: aiosqlite.Connection,
    category: str,
    rule: str,
    instruction: str,
    confidence: str = "high",
) -> None:
    """Store or update a user preference."""
    now = iso()
    await db.execute(
        """INSERT OR REPLACE INTO preferences
           (category, rule, instruction, confidence, last_updated)
           VALUES (?, ?, ?, ?, ?)""",
        (category, rule, instruction, confidence, now),
    )
    await db.commit()


async def find_preferences(db: aiosqlite.Connection, query: str) -> list[dict[str, Any]]:
    """Search preferences by keyword in category, rule, or instruction."""
    like = f"%{query.strip()}%"
    async with db.execute(
        """SELECT category, rule, instruction, confidence, last_updated FROM preferences
           WHERE category LIKE ? OR rule LIKE ? OR instruction LIKE ?
           ORDER BY last_updated DESC LIMIT 10""",
        (like, like, like),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_preferences_context(db: aiosqlite.Connection) -> dict[str, Any]:
    """Compile structured user preferences to inject into LLM router and reasoning prompts."""
    prefs = await get_all_preferences(db)
    rules_list: list[str] = []
    app_preferences: dict[str, str] = {}
    workspace_preferences: dict[str, int] = {}

    for p in prefs:
        cat = p.get("category", "")
        rule = p.get("rule", "")
        instruction = p.get("instruction", "")
        summary = instruction or rule

        if cat == "app":
            app_preferences[rule] = instruction
        elif cat == "workspace":
            try:
                workspace_preferences[rule] = int(instruction)
            except ValueError:
                rules_list.append(summary)
        else:
            if summary:
                rules_list.append(summary)

    return {
        "rules": rules_list,
        "app_preferences": app_preferences,
        "workspace_preferences": workspace_preferences,
    }
