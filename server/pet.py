"""Tamagotchi state machine (spec §7) persisted in ``pet_state`` and pushed over the WebSocket.

Spec transitions plus three documented extensions for text input / confirmations:
``IDLE→THINKING`` (typed text skips LISTENING), ``THINKING→ERROR`` (nothing could handle the
request) and ``THINKING→IDLE`` (waiting on a user confirmation, or a cancelled request).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Any

import aiosqlite

from server.db import iso, utcnow

log = logging.getLogger(__name__)


class PetState(StrEnum):
    SLEEPING = "sleeping"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    WORKING = "working"
    SUCCESS = "success"
    ERROR = "error"


S = PetState
TRANSITIONS: dict[PetState, frozenset[PetState]] = {
    S.SLEEPING: frozenset({S.IDLE}),
    S.IDLE: frozenset({S.LISTENING, S.SLEEPING, S.THINKING}),
    S.LISTENING: frozenset({S.THINKING, S.IDLE}),
    S.THINKING: frozenset({S.WORKING, S.ERROR, S.IDLE}),
    S.WORKING: frozenset({S.SUCCESS, S.ERROR}),
    S.SUCCESS: frozenset({S.IDLE}),
    S.ERROR: frozenset({S.IDLE}),
}

MOOD_FOR_STATE: dict[PetState, str] = {
    S.SLEEPING: "tired",
    S.IDLE: "happy",
    S.LISTENING: "curious",
    S.THINKING: "curious",
    S.WORKING: "focused",
    S.SUCCESS: "happy",
    S.ERROR: "confused",
}


class InvalidTransition(ValueError):
    pass


def can_transition(src: PetState, dst: PetState) -> bool:
    return dst in TRANSITIONS[src]


def evolution_stage(age_days: int) -> str:
    """Egg (1-2) -> Baby (3-13) -> Young (14-29) -> Adult (30+)."""
    if age_days <= 2:
        return "egg"
    if age_days <= 13:
        return "baby"
    if age_days <= 29:
        return "young"
    return "adult"


def age_in_days(created_at: str, today: date | None = None) -> int:
    created = datetime.fromisoformat(created_at).date()
    return max(1, ((today or utcnow().date()) - created).days + 1)


def _clamp(v: int) -> int:
    return max(0, min(100, v))


Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


class PetService:
    def __init__(
        self,
        db: aiosqlite.Connection,
        broadcast: Broadcast | None = None,
        *,
        success_timeout_s: float = 3.0,
        sleep_after_s: float = 30 * 60,
    ) -> None:
        self.db = db
        self.broadcast = broadcast
        self.success_timeout_s = success_timeout_s
        self.sleep_after_s = sleep_after_s
        self._timer: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def row(self) -> dict[str, Any]:
        async with self.db.execute("SELECT * FROM pet_state WHERE id = 1") as cur:
            r = await cur.fetchone()
        assert r is not None, "pet_state row missing"
        return dict(r)

    async def state(self) -> PetState:
        return PetState((await self.row())["state"])

    async def snapshot(self) -> dict[str, Any]:
        r = await self.row()
        age = age_in_days(r["created_at"])
        stage = evolution_stage(age)
        if age != r["age_days"] or stage != r["evolution_stage"]:
            await self.db.execute(
                "UPDATE pet_state SET age_days = ?, evolution_stage = ? WHERE id = 1",
                (age, stage),
            )
            await self.db.commit()
        return {
            "type": "pet",
            "state": r["state"],
            "mood": r["mood"],
            "pet_name": r["pet_name"],
            "species": r["species"],
            "evolution_stage": stage,
            "age_days": age,
            "happiness": r["happiness"],
            "bond_level": r["bond_level"],
            "interaction_count": r["interaction_count"],
            "favorite_actions": json.loads(r["favorite_actions"] or "[]"),
        }

    async def push(self) -> dict[str, Any]:
        snap = await self.snapshot()
        if self.broadcast:
            await self.broadcast(snap)
        return snap

    async def transition(self, dst: PetState, *, force: bool = False) -> bool:
        """Move to ``dst`` if allowed; persist + broadcast. Returns whether state changed."""
        async with self._lock:
            src = await self.state()
            if src == dst:
                return False
            if not force and not can_transition(src, dst):
                raise InvalidTransition(f"{src} -> {dst}")
            await self.db.execute(
                "UPDATE pet_state SET state = ?, mood = ? WHERE id = 1",
                (dst.value, MOOD_FOR_STATE[dst]),
            )
            await self.db.commit()
        self._schedule_followup(dst)
        await self.push()
        return True

    async def wake(self) -> None:
        """App opened / screen on, or a new request arrived: get to IDLE."""
        st = await self.state()
        if st in (S.SLEEPING, S.SUCCESS, S.ERROR, S.LISTENING):
            await self.transition(S.IDLE)
        elif st in (S.THINKING, S.WORKING):
            # A previous request died mid-flight; recover.
            await self.transition(S.IDLE, force=True)

    async def begin_request(self) -> None:
        await self.wake()
        await self.transition(S.THINKING)

    async def acknowledge_error(self) -> None:
        if await self.state() == S.ERROR:
            await self.transition(S.IDLE)

    async def record_interaction(self, *, success: bool, tool: str | None) -> None:
        r = await self.row()
        happiness = _clamp(r["happiness"] + (2 if success else -3))
        bond = _clamp(r["bond_level"] + (1 if success else 0))
        await self.db.execute(
            """UPDATE pet_state SET interaction_count = interaction_count + 1,
               happiness = ?, bond_level = ?, last_interaction = ?, favorite_actions = ?
               WHERE id = 1""",
            (happiness, bond, iso(), json.dumps(await self._favorites())),
        )
        await self.db.commit()

    async def _favorites(self, limit: int = 5) -> list[str]:
        async with self.db.execute(
            """SELECT tool_name, COUNT(*) AS n FROM commands_log
               WHERE status = 'success' AND tool_name IS NOT NULL
                 AND tool_name NOT IN ('cache', 'zero_tool', 'memory')
               GROUP BY tool_name ORDER BY n DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [row["tool_name"] for row in await cur.fetchall()]

    # -- timers -------------------------------------------------------------------------

    def _schedule_followup(self, dst: PetState) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None
        if dst == S.SUCCESS:
            self._timer = asyncio.create_task(
                self._after(self.success_timeout_s, S.SUCCESS, S.IDLE)
            )
        elif dst == S.IDLE:
            self._timer = asyncio.create_task(self._after(self.sleep_after_s, S.IDLE, S.SLEEPING))

    async def _after(self, delay: float, expect: PetState, dst: PetState) -> None:
        try:
            await asyncio.sleep(delay)
            if await self.state() == expect:
                await self.transition(dst)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive; timers must never crash the app
            log.exception("pet timer failed")

    async def close(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._timer
