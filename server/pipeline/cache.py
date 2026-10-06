"""Stage 1: TTL cache in the SQLite ``cache`` table.

Key namespaces:
- ``result:<text>`` — final answers of read-only tools (side-effecting tools are never cached).
- ``route:<text>``  — Groq router classifications, so the same phrase costs tokens only once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from server.db import utcnow
from server.pipeline.types import Outcome


class Cache:
    def __init__(self, db: aiosqlite.Connection, now: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.now = now

    async def get(self, key: str) -> Any | None:
        async with self.db.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= self.now():
            await self.db.execute("DELETE FROM cache WHERE key = ?", (key,))
            await self.db.commit()
            return None
        return json.loads(row["value"])

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        expires = (self.now() + timedelta(seconds=ttl_s)).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), expires),
        )
        await self.db.commit()

    async def purge_expired(self) -> int:
        cur = await self.db.execute(
            "DELETE FROM cache WHERE expires_at <= ?", (self.now().isoformat(),)
        )
        await self.db.commit()
        return cur.rowcount


def result_key(text: str) -> str:
    return f"result:{text}"


def route_key(text: str) -> str:
    return f"route:{text}"


async def lookup(cache: Cache, text: str) -> Outcome | None:
    hit = await cache.get(result_key(text))
    if not hit:
        return None
    return Outcome(
        text=hit["text"],
        stage="cache",
        intent=hit.get("tool") or "cache",
        tool="cache",
        args=hit.get("args", {}),
    )


async def store(cache: Cache, text: str, outcome: Outcome, ttl_s: float) -> None:
    await cache.set(
        result_key(text), {"text": outcome.text, "tool": outcome.tool, "args": outcome.args}, ttl_s
    )
