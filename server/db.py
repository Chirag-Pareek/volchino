"""SQLite memory (spec §5, §9): WAL mode, schema auto-creation, seeded pet_state row."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS pet_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pet_name TEXT NOT NULL DEFAULT 'Volchino',
    species TEXT NOT NULL DEFAULT 'CyberCat',
    evolution_stage TEXT NOT NULL DEFAULT 'egg',
    age_days INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'sleeping',
    mood TEXT NOT NULL DEFAULT 'tired',
    happiness INTEGER NOT NULL DEFAULT 70,
    energy INTEGER NOT NULL DEFAULT 100,
    hunger INTEGER NOT NULL DEFAULT 0,
    bond_level INTEGER NOT NULL DEFAULT 10,
    playfulness INTEGER NOT NULL DEFAULT 50,
    curiosity INTEGER NOT NULL DEFAULT 50,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    favorite_actions TEXT NOT NULL DEFAULT '[]',
    last_interaction TIMESTAMP,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    trigger TEXT NOT NULL,
    parameters TEXT NOT NULL DEFAULT '{}',
    required_tools TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT 'requires_confirmation'
        CHECK (permissions IN ('safe', 'requires_confirmation')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    success_criteria TEXT NOT NULL DEFAULT '',
    last_used TIMESTAMP,
    success_rate REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS generated_images (
    image_hash TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    resolution TEXT,
    aspect_ratio TEXT,
    file_path TEXT,
    vault_asset_path TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT,
    window_title TEXT,
    workspace TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    idle_seconds INTEGER NOT NULL DEFAULT 0,
    project_tag TEXT,
    timestamp TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(timestamp);

CREATE TABLE IF NOT EXISTS preferences (
    category TEXT NOT NULL,
    rule TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'inferred'
        CHECK (confidence IN ('high', 'medium', 'inferred')),
    last_updated TIMESTAMP NOT NULL,
    PRIMARY KEY (category, rule)
);

CREATE TABLE IF NOT EXISTS commands_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_input TEXT NOT NULL,
    intent TEXT,
    tool_name TEXT,
    tool_args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result_summary TEXT,
    duration_ms REAL NOT NULL DEFAULT 0,
    timestamp TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands_log(timestamp);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL
);
"""

TABLES = (
    "pet_state",
    "skills",
    "generated_images",
    "activity",
    "preferences",
    "commands_log",
    "cache",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


async def connect(path: Path | str) -> aiosqlite.Connection:
    """Open the database, enable WAL, create the schema and seed pet_state."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.executescript(SCHEMA)
    await db.execute(
        "INSERT OR IGNORE INTO pet_state (id, created_at) VALUES (1, ?)",
        (iso(),),
    )
    await db.commit()
    return db


async def log_command(
    db: aiosqlite.Connection,
    *,
    user_input: str,
    intent: str | None,
    tool_name: str | None,
    tool_args: dict | None,
    status: str,
    result_summary: str,
    duration_ms: float,
) -> int:
    cur = await db.execute(
        """INSERT INTO commands_log
           (user_input, intent, tool_name, tool_args, status, result_summary, duration_ms,
            timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_input,
            intent,
            tool_name,
            json.dumps(tool_args or {}, sort_keys=True),
            status,
            result_summary[:500],
            round(duration_ms, 3),
            iso(),
        ),
    )
    await db.commit()
    return cur.lastrowid or 0
