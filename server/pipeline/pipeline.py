"""Pipeline orchestrator: runs the stages in spec order, logs, and drives the pet."""

from __future__ import annotations

import asyncio
import logging
import time

import aiosqlite

from server.db import log_command
from server.llm.opencode import SkillProposer
from server.permissions import Decision
from server.pet import PetService, PetState
from server.pipeline import cache as cache_stage
from server.pipeline import deterministic, fallback, memory, skills
from server.pipeline.cache import Cache
from server.pipeline.executor import Executor
from server.pipeline.normalize import normalize
from server.pipeline.router import GroqRouter
from server.pipeline.types import Outcome, PendingAction, Plan, ToolCall
from server.tools.base import Tool

log = logging.getLogger(__name__)

PENDING_TTL_S = 300
# Deterministic matches on free-text targets fall through to the router if args are invalid
# ("start a timer" should not become open_app("a timer")).
_FUZZY_TOOLS = {"open_app", "focus_window", "open_url"}


class Pipeline:
    def __init__(
        self,
        *,
        db: aiosqlite.Connection,
        registry: dict[str, Tool],
        executor: Executor,
        cache: Cache,
        router: GroqRouter,
        proposer: SkillProposer,
        pet: PetService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.executor = executor
        self.cache = cache
        self.router = router
        self.proposer = proposer
        self.pet = pet
        self.pending: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()

    # -- public -------------------------------------------------------------------------

    async def run(self, raw: str) -> Outcome:
        async with self._lock:
            t0 = time.perf_counter()
            text = normalize(raw)
            if not text:
                return Outcome(
                    text="I didn't catch that.", stage="normalize", intent="empty", status="failed"
                )
            await self.pet.begin_request()
            try:
                outcome = await self._resolve(raw, text)
            except Exception:
                log.exception("pipeline crashed")
                outcome = Outcome(
                    text="Something went wrong inside me.",
                    stage="error",
                    intent="error",
                    status="failed",
                )
                await self._settle_pet(outcome, executed=False)
            await self._finalize(raw, outcome, t0)
            return outcome

    async def resolve_confirmation(self, pending_id: str, approved: bool) -> Outcome:
        async with self._lock:
            t0 = time.perf_counter()
            self._expire_pending()
            pending = self.pending.pop(pending_id, None)
            if pending is None:
                return Outcome(
                    text="That confirmation expired or doesn't exist.",
                    stage="confirm",
                    intent="confirm",
                    status="failed",
                )
            if not approved:
                outcome = Outcome(
                    text="Okay, cancelled.",
                    stage="confirm",
                    intent=f"declined:{pending.kind}",
                    status="permission_denied",
                    tool=self._plan_tool(pending.plan),
                )
                await self._finalize(pending.user_input, outcome, t0, plan=pending.plan)
                return outcome
            await self.pet.begin_request()
            if pending.kind == "approve_skill":
                ok = await skills.approve(self.db, pending.skill_name or "")
                outcome = Outcome(
                    text=f"Skill '{pending.skill_name}' approved."
                    if ok
                    else "Couldn't approve that skill.",
                    stage="confirm",
                    intent="approve_skill",
                    status="success" if ok else "failed",
                )
                await self._settle_pet(outcome, executed=True)
            else:
                assert pending.plan is not None
                outcome = await self._execute(pending.plan, pending.text)
            await self._finalize(pending.user_input, outcome, t0, plan=pending.plan)
            return outcome

    # -- stages -------------------------------------------------------------------------

    async def _resolve(self, raw: str, text: str) -> Outcome:
        hit = await cache_stage.lookup(self.cache, text)
        if hit:
            await self._settle_pet(hit, executed=True)
            return hit

        mem = await memory.handle(self.db, text)
        if mem:
            await self._settle_pet(mem, executed=True)
            return mem

        plan = await skills.match(self.db, text)
        if plan:
            return await self._dispatch(raw, text, plan)

        call = deterministic.match(text)
        if call:
            plan = Plan([call], stage="tool", intent=call.tool)
            if call.tool not in _FUZZY_TOOLS or self.executor.validate(plan) is None:
                return await self._dispatch(raw, text, plan)

        tokens = 0
        routed = await self.router.route(text)
        if routed is not None:
            tokens = routed.tokens_used
            if routed.call is not None:
                plan = Plan(
                    [routed.call], stage="router", intent=routed.call.tool, tokens_used=tokens
                )
                return await self._dispatch(raw, text, plan)
            if getattr(routed, "skill_name", None):
                from server.skills.engine import get_skill

                s = await get_skill(self.db, routed.skill_name)
                if s and s.get("status") == "approved":
                    steps = s.get("steps", [])
                    calls = [
                        ToolCall(st["tool"], st.get("args", {}))
                        for st in steps
                        if isinstance(st, dict) and "tool" in st
                    ]
                    if calls:
                        plan = Plan(
                            calls=calls,
                            stage="skill",
                            intent=f"skill:{routed.skill_name}",
                            skill_name=routed.skill_name,
                            tokens_used=tokens,
                            skill_requires_confirmation=s.get("permissions") != "safe",
                        )
                        return await self._dispatch(raw, text, plan)

        outcome = await fallback.handle(
            self.db, self.proposer, self.registry, user_input=raw, text=text, tokens_so_far=tokens
        )
        await self._park(outcome)
        return outcome

    async def _dispatch(self, raw: str, text: str, plan: Plan) -> Outcome:
        err = self.executor.validate(plan)
        if err:
            outcome = Outcome(
                text=err,
                stage=plan.stage,
                intent=plan.intent,
                status="failed",
                tool=self._plan_tool(plan),
                tokens_used=plan.tokens_used,
                args=self._plan_args(plan),
            )
            await self._settle_pet(outcome, executed=False)
            return outcome
        if self.executor.gate(plan) is Decision.CONFIRM:
            pending = PendingAction(
                kind="plan",
                action=self.executor.describe(plan),
                user_input=raw,
                text=text,
                plan=plan,
            )
            outcome = Outcome(
                text=f"Needs your confirmation: {pending.action}",
                stage=plan.stage,
                intent=plan.intent,
                status="pending_confirmation",
                tool=self._plan_tool(plan),
                args=self._plan_args(plan),
                tokens_used=plan.tokens_used,
                pending=pending,
            )
            await self._park(outcome)
            return outcome
        return await self._execute(plan, text)

    async def _execute(self, plan: Plan, text: str) -> Outcome:
        await self.pet.transition(PetState.WORKING)
        res = await self.executor.run(plan)
        outcome = Outcome(
            text=res.text,
            stage=plan.stage,
            intent=plan.intent,
            status="success" if res.ok else "failed",
            tool=res.tool,
            args=self._plan_args(plan),
            tokens_used=plan.tokens_used,
        )
        if plan.skill_name:
            await skills.record_result(self.db, plan.skill_name, res.ok)
        elif res.ok and len(plan.calls) == 1:
            ttl = self.registry[plan.calls[0].tool].cache_ttl_s
            if ttl:
                await cache_stage.store(self.cache, text, outcome, ttl)
        await self.pet.transition(PetState.SUCCESS if res.ok else PetState.ERROR)
        return outcome

    # -- helpers ------------------------------------------------------------------------

    async def _park(self, outcome: Outcome) -> None:
        assert outcome.pending is not None
        self._expire_pending()
        self.pending[outcome.pending.id] = outcome.pending
        await self.pet.transition(PetState.IDLE)

    def _expire_pending(self) -> None:
        now = time.monotonic()
        for pid in [p for p, a in self.pending.items() if now - a.created > PENDING_TTL_S]:
            del self.pending[pid]

    async def _settle_pet(self, outcome: Outcome, *, executed: bool) -> None:
        state = await self.pet.state()
        if state == PetState.THINKING:
            if executed and outcome.status == "success":
                await self.pet.transition(PetState.WORKING)
            else:
                await self.pet.transition(PetState.ERROR)
                return
        if await self.pet.state() == PetState.WORKING:
            await self.pet.transition(
                PetState.SUCCESS if outcome.status == "success" else PetState.ERROR
            )

    async def _finalize(
        self, raw: str, outcome: Outcome, t0: float, plan: Plan | None = None
    ) -> None:
        duration_ms = (time.perf_counter() - t0) * 1000
        tool_name = outcome.tool or ("cache" if outcome.stage == "cache" else None)
        await log_command(
            self.db,
            user_input=raw,
            intent=outcome.intent,
            tool_name=tool_name,
            tool_args=outcome.args or self._plan_args(plan),
            status=outcome.status,
            result_summary=outcome.text,
            duration_ms=duration_ms,
        )
        if outcome.status in ("success", "failed"):
            await self.pet.record_interaction(success=outcome.status == "success", tool=tool_name)
        await self.pet.push()

    @staticmethod
    def _plan_tool(plan: Plan | None) -> str | None:
        if plan is None or not plan.calls:
            return None
        return plan.calls[0].tool if len(plan.calls) == 1 else f"skill:{plan.skill_name}"

    @staticmethod
    def _plan_args(plan: Plan | None) -> dict:
        if plan is None or not plan.calls:
            return {}
        return (
            dict(plan.calls[0].args)
            if len(plan.calls) == 1
            else {"steps": [{"tool": c.tool, "args": c.args} for c in plan.calls]}
        )
