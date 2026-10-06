"""Stage 2: answer from SQLite memory (command history, preferences, skills, pet) — 0 tokens."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import aiosqlite

from server.db import iso
from server.pipeline.types import Outcome

Handler = Callable[[aiosqlite.Connection, re.Match[str]], Awaitable[str]]


async def _last_command(db: aiosqlite.Connection, _: re.Match[str]) -> str:
    async with db.execute(
        "SELECT user_input, status FROM commands_log ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return "You haven't given me any commands yet."
    return f"Your last command was {row['user_input']!r} ({row['status']})."


async def _commands_today(db: aiosqlite.Connection, _: re.Match[str]) -> str:
    async with db.execute(
        "SELECT COUNT(*) AS n FROM commands_log WHERE date(timestamp, 'localtime') = "
        "date('now', 'localtime')"
    ) as cur:
        row = await cur.fetchone()
    n = row["n"] if row else 0
    return f"You've run {n} command{'s' if n != 1 else ''} today."


async def _pet_status(db: aiosqlite.Connection, _: re.Match[str]) -> str:
    async with db.execute(
        "SELECT pet_name, mood, happiness, bond_level, interaction_count FROM pet_state "
        "WHERE id = 1"
    ) as cur:
        r = await cur.fetchone()
    assert r is not None
    return (
        f"{r['pet_name']} is feeling {r['mood']} — happiness {r['happiness']}, "
        f"bond {r['bond_level']}, {r['interaction_count']} interactions so far."
    )


async def _recall(db: aiosqlite.Connection, m: re.Match[str]) -> str:
    topic = m.group("topic").strip()
    like = f"%{topic}%"
    async with db.execute(
        "SELECT rule, instruction FROM preferences WHERE category LIKE ? OR rule LIKE ? "
        "OR instruction LIKE ? ORDER BY last_updated DESC LIMIT 5",
        (like, like, like),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return f"I don't have anything remembered about {topic}."
    return "; ".join(r["instruction"] or r["rule"] for r in rows)


async def _remember(db: aiosqlite.Connection, m: re.Match[str]) -> str:
    fact = m.group("fact").strip()
    await db.execute(
        "INSERT OR REPLACE INTO preferences (category, rule, instruction, confidence, "
        "last_updated) VALUES ('explicit', ?, ?, 'high', ?)",
        (fact, fact, iso()),
    )
    await db.commit()
    return f"Got it, I'll remember that {fact}."


async def _list_skills(db: aiosqlite.Connection, _: re.Match[str]) -> str:
    async with db.execute("SELECT name, status FROM skills ORDER BY status, name") as cur:
        rows = await cur.fetchall()
    if not rows:
        return "No skills yet."
    return "Skills: " + ", ".join(f"{r['name']} ({r['status']})" for r in rows)


PATTERNS: list[tuple[re.Pattern[str], str, Handler]] = [
    (
        re.compile(r"^what(?: was|'s| is) my (?:last|previous) command$"),
        "last_command",
        _last_command,
    ),
    (
        re.compile(r"^how many commands(?: did i (?:run|give))?(?: today)?$"),
        "commands_today",
        _commands_today,
    ),
    (
        re.compile(r"^(?:how are you|pet status|status|how is volchino|how's volchino)$"),
        "pet_status",
        _pet_status,
    ),
    (re.compile(r"^what do you (?:know|remember) about (?P<topic>.+)$"), "recall", _recall),
    (re.compile(r"^remember (?:that )?(?P<fact>.{3,})$"), "remember", _remember),
    (re.compile(r"^(?:list|show)(?: my)? (?:skills|drafts)$"), "list_skills", _list_skills),
]


async def handle(db: aiosqlite.Connection, text: str) -> Outcome | None:
    for pattern, intent, fn in PATTERNS:
        m = pattern.fullmatch(text)
        if m:
            return Outcome(text=await fn(db, m), stage="memory", intent=intent, tool="zero_tool")
    return None
