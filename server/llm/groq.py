"""Groq chat-completions client (OpenAI-compatible) over httpx."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResponse:
    content: str
    total_tokens: int


class ChatClient(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> ChatResponse: ...


class GroqClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = GROQ_BASE_URL,
        timeout_s: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GroqClient requires an API key")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    async def complete(self, messages: list[dict[str, str]]) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        try:
            resp = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"groq request failed: {type(e).__name__}") from e
        if resp.status_code != 200:
            # Never echo headers (they contain the key); status code is enough.
            raise LLMError(f"groq returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
            tokens = int(data.get("usage", {}).get("total_tokens", 0))
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("malformed groq response") from e
        return ChatResponse(content=content, total_tokens=tokens)

    async def aclose(self) -> None:
        await self._client.aclose()
