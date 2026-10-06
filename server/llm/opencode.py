"""OpenCode Go heavy-reasoning fallback — STUB.

TODO(phase-5+): replace ``OpenCodeStub`` with a real client that sends the request plus the
tool catalog to OpenCode Go (key: ``OPENCODE_API_KEY``) and asks it to propose a skill made only
of registered tool steps. Whatever it returns must still be saved with ``status='draft'`` and
only run after explicit user approval (spec §10). Never execute model output directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SkillDraft:
    name: str
    description: str
    trigger: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    permissions: str = "requires_confirmation"
    success_criteria: str = ""
    tokens_used: int = 0


class SkillProposer(Protocol):
    async def propose_skill(self, text: str, tools: dict[str, Any]) -> SkillDraft: ...


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len].rstrip("_") or "skill"


class OpenCodeStub:
    """Returns an empty draft skill without calling any model (0 tokens)."""

    async def propose_skill(self, text: str, tools: dict[str, Any]) -> SkillDraft:
        # TODO(phase-5+): call OpenCode Go here.
        return SkillDraft(
            name=f"draft_{slugify(text)}",
            description=f"TODO: auto-drafted for request {text!r}; steps need to be authored.",
            trigger=text,
            steps=[],
            success_criteria="TODO",
        )
