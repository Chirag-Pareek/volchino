"""Shared pipeline data types."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """A list of tool calls chosen by the skill, deterministic or router stage."""

    calls: list[ToolCall]
    stage: str
    intent: str
    tokens_used: int = 0
    skill_name: str | None = None
    skill_requires_confirmation: bool = False


PendingKind = Literal["plan", "approve_skill"]


@dataclass
class PendingAction:
    kind: PendingKind
    action: str  # human-readable description shown in the confirm dialog
    user_input: str
    text: str
    plan: Plan | None = None
    skill_name: str | None = None
    skill_proposal: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.monotonic)


Status = Literal["success", "failed", "permission_denied", "pending_confirmation"]


@dataclass
class Outcome:
    text: str
    stage: str
    intent: str
    status: Status = "success"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    pending: PendingAction | None = None
    skill_proposal: dict[str, Any] | None = None

    def result_message(self) -> dict[str, Any]:
        return {
            "type": "result",
            "text": self.text,
            "tool": self.tool,
            "tokens_used": self.tokens_used,
            "status": self.status,
            "stage": self.stage,
        }
