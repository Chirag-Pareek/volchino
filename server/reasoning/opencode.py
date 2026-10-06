"""OpenCode Go deep reasoning client (Tier 6).

Triggered ONLY when Tier 1-5 cannot satisfy the request.
Formulates multi-step action plans, synthesizes complex answers, and compiles
repetitive multi-step tasks into reusable draft skill proposals.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from server.llm.opencode import SkillDraft, slugify
from server.skills.engine import synthesize_skill

log = logging.getLogger(__name__)

OPENCODE_BASE_URL = "https://api.opencode.ai/v1"


@dataclass
class ReasoningResult:
    answer: str
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    skill_proposal: dict[str, Any] | None = None
    tokens_used: int = 0


class OpenCodeReasoningClient:
    """Tier 6 Deep Reasoning Engine using OpenCode Go.

    Takes user input, the registered tool catalog, and preferences context.
    Produces either a direct reasoning answer or a multi-step tool plan,
    synthesizing it into a draft skill definition for user approval.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = OPENCODE_BASE_URL,
        model: str = "opencode-go",
        http_client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = http_client

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()

    async def reason(
        self,
        prompt: str,
        tool_catalog: dict[str, Any],
        preferences: dict[str, Any] | None = None,
    ) -> ReasoningResult:
        """Run deep reasoning on a complex or multi-step request."""
        # If API key is present, attempt remote API call
        if self.enabled:
            try:
                return await self._call_opencode_api(prompt, tool_catalog, preferences)
            except Exception as e:
                log.warning("OpenCode API call failed: %s; falling back to local synthesis", e)

        # Local deterministic decomposition & skill formulation fallback
        return self._local_reasoning_synthesis(prompt, tool_catalog, preferences)

    async def propose_skill(self, text: str, tools: dict[str, Any]) -> SkillDraft:
        """Conforms to the SkillProposer protocol used by pipeline fallback."""
        res = await self.reason(text, tools)
        proposal = res.skill_proposal or {}
        return SkillDraft(
            name=proposal.get("name", f"draft_{slugify(text)}"),
            description=proposal.get("description", f"Skill for: {text}"),
            trigger=proposal.get("trigger", text),
            steps=proposal.get("steps", res.plan_steps),
            required_tools=proposal.get("required_tools", []),
            parameters=proposal.get("parameters", {}),
            permissions=proposal.get("permissions", "requires_confirmation"),
            success_criteria=proposal.get("success_criteria", "Completed successfully"),
            tokens_used=res.tokens_used,
        )

    async def _call_opencode_api(
        self,
        prompt: str,
        tool_catalog: dict[str, Any],
        preferences: dict[str, Any] | None = None,
    ) -> ReasoningResult:
        """Call OpenCode Go API with structured instructions."""
        system_prompt = (
            "You are the deep reasoning tier of the Volchino AI agent on Arch Linux.\n"
            "Given the user request, tool catalog, and user preferences, devise a plan.\n"
            "If the request requires executing actions, construct a list of steps using ONLY tools "
            "from the catalog.\n"
            f"TOOL CATALOG: {json.dumps(tool_catalog)}\n"
            f"PREFERENCES: {json.dumps(preferences or {})}\n"
            "Reply with a JSON object: {\n"
            '  "answer": "Human-friendly explanation or answer",\n'
            '  "steps": [{"tool": "<name>", "args": {...}}],\n'
            '  "make_skill": true/false,\n'
            '  "skill_name": "<short_snake_case_name>",\n'
            '  "skill_description": "<description>"\n'
            "}"
        )

        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return self._parse_reasoning_json(prompt, raw_content, tool_catalog, tokens)
        finally:
            if not self._client:
                await client.aclose()

    def _parse_reasoning_json(
        self,
        prompt: str,
        raw_content: str,
        tool_catalog: dict[str, Any],
        tokens: int,
    ) -> ReasoningResult:
        clean = re.sub(r"^```(?:json)?|```$", "", raw_content.strip(), flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(clean)
        except Exception:
            return ReasoningResult(answer=raw_content.strip(), tokens_used=tokens)

        answer = parsed.get("answer", "Here is what I came up with.")
        raw_steps = parsed.get("steps") or []
        valid_steps = [
            s for s in raw_steps if isinstance(s, dict) and s.get("tool") in tool_catalog
        ]

        skill_proposal = None
        if valid_steps and (parsed.get("make_skill") or len(valid_steps) > 1):
            skill_proposal = synthesize_skill(
                task_description=parsed.get("skill_description", prompt),
                steps=valid_steps,
                trigger=prompt,
                name=parsed.get("skill_name"),
            )

        return ReasoningResult(
            answer=answer,
            plan_steps=valid_steps,
            skill_proposal=skill_proposal,
            tokens_used=tokens,
        )

    def _local_reasoning_synthesis(
        self,
        prompt: str,
        tool_catalog: dict[str, Any],
        preferences: dict[str, Any] | None = None,
    ) -> ReasoningResult:
        """Offline / local heuristic reasoning to formulate steps and draft skills."""
        lower = prompt.lower()
        steps: list[dict[str, Any]] = []

        # Heuristic 1: Workspace setup / workstation routine
        if any(w in lower for w in ("workstation", "dev mode", "setup work", "start work")):
            if "switch_workspace" in tool_catalog:
                target_ws = (preferences or {}).get("workspace_preferences", {}).get("work", 1)
                steps.append({"tool": "switch_workspace", "args": {"workspace": target_ws}})
            if "open_app" in tool_catalog:
                fav_app = (preferences or {}).get("app_preferences", {}).get("editor", "code")
                steps.append({"tool": "open_app", "args": {"app": fav_app}})
                steps.append({"tool": "open_app", "args": {"app": "firefox"}})

        # Heuristic 2: Focus Obsidian / note-taking mode
        elif any(w in lower for w in ("take notes", "note mode", "study mode")):
            if "focus_window" in tool_catalog:
                steps.append({"tool": "focus_window", "args": {"window_class": "obsidian"}})

        # Heuristic 3: Entertainment / relax routine
        elif any(w in lower for w in ("relax", "music mode", "chill")):
            if "set_volume" in tool_catalog:
                steps.append({"tool": "set_volume", "args": {"level": 40}})

        skill_proposal = None
        if steps:
            skill_proposal = synthesize_skill(
                task_description=f"Automated routine for: {prompt}",
                steps=steps,
                trigger=prompt,
            )
            answer = (
                f"I formulated a multi-step routine with {len(steps)} action(s). "
                f"Proposed draft skill '{skill_proposal['name']}'."
            )
        else:
            answer = (
                f"I don't know how to satisfy '{prompt}' yet. "
                "I've saved a draft skill so you can author or approve it."
            )
            skill_proposal = synthesize_skill(
                task_description=f"Draft skill for request: {prompt}",
                steps=[],
                trigger=prompt,
            )

        return ReasoningResult(
            answer=answer,
            plan_steps=steps,
            skill_proposal=skill_proposal,
            tokens_used=0,
        )
