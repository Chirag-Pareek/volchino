"""Phase 8: Social media drafting pipeline for X (Twitter) and LinkedIn."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite

from server.config import Settings
from server.image.flux import FluxImageGenerator, ImageResult

log = logging.getLogger(__name__)

PLATFORM_LIMITS = {
    "x": 280,
    "twitter": 280,
    "linkedin": 3000,
}


class SocialError(RuntimeError):
    """Base exception for social pipeline errors."""


@dataclass(frozen=True)
class SocialDraft:
    platform: str
    title: str
    text: str
    image_asset: str | None
    draft_path: Path
    char_count: int
    status: str
    is_thread: bool = False
    thread_count: int = 1


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", cleaned)[:30] or "post"


def split_x_thread(text: str, limit: int = 280) -> list[str]:
    """Split text into numbered thread parts if longer than limit."""
    if len(text) <= limit:
        return [text]

    words = text.split()
    parts: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) > limit - 6:  # Reserve space for "(1/N)"
            if current:
                parts.append(" ".join(current))
                current = [word]
            else:
                parts.append(word[:limit - 6])
                current = [word[limit - 6:]]
        else:
            current.append(word)

    if current:
        parts.append(" ".join(current))

    n = len(parts)
    return [f"{p} ({i + 1}/{n})" for i, p in enumerate(parts)]


async def create_social_draft(
    settings: Settings,
    db: aiosqlite.Connection,
    platform: Literal["x", "twitter", "linkedin"],
    text: str,
    image_prompt: str | None = None,
    image_generator: FluxImageGenerator | None = None,
) -> SocialDraft:
    """Draft a social post in Obsidian Vault with optional 16:9 FLUX image generation."""
    clean_platform = "x" if platform.lower() in ("x", "twitter") else "linkedin"
    clean_text = text.strip()
    if not clean_text:
        raise SocialError("Social post content cannot be empty")

    now = datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    slug = _slugify(clean_text)

    # 1. Visual Generation if requested (always 16:9 for social cards)
    image_asset_path: str | None = None
    if image_prompt and image_generator:
        log.info("Generating 16:9 social visual for draft: %r", image_prompt)
        res: ImageResult = await image_generator.generate_image(
            prompt=image_prompt,
            aspect_ratio="16:9",
            enhance=True,
        )
        image_asset_path = res.vault_asset_path or str(res.file_path)

    # 2. Check thread length for X
    limit = PLATFORM_LIMITS[clean_platform]
    is_thread = False
    thread_parts = [clean_text]
    if clean_platform == "x" and len(clean_text) > limit:
        is_thread = True
        thread_parts = split_x_thread(clean_text, limit=limit)

    # 3. Format markdown note
    img_embed = ""
    if image_asset_path:
        img_embed = f"\n## Attached Visual (16:9 Social Card)\n![[{Path(image_asset_path).name}]]\n"

    body = "\n\n---\n\n".join(thread_parts) if is_thread else clean_text
    thread_desc = f"Thread: {len(thread_parts)} parts" if is_thread else "Single Post"

    frontmatter = f"""---
title: "Social Draft: {slug}"
platform: {clean_platform}
status: draft
created_at: {now.isoformat()}
character_count: {len(clean_text)}
is_thread: {str(is_thread).lower()}
image_asset: "{image_asset_path or ''}"
---

# {clean_platform.upper()} Post Draft ({thread_desc})

{body}
{img_embed}
"""

    # 4. Save to Obsidian Vault or local drafts
    drafts_dir = settings.sqlite_path.parent / "social" / "drafts"
    if settings.vault_path:
        vault_drafts = settings.vault_path / "Social" / "Drafts"
        vault_drafts.mkdir(parents=True, exist_ok=True)
        draft_file = vault_drafts / f"{date_str}_{clean_platform}_{slug}.md"
    else:
        drafts_dir.mkdir(parents=True, exist_ok=True)
        draft_file = drafts_dir / f"{date_str}_{clean_platform}_{slug}.md"

    # Also mirror locally
    drafts_dir.mkdir(parents=True, exist_ok=True)
    local_mirror = drafts_dir / f"{date_str}_{clean_platform}_{slug}.md"
    local_mirror.write_text(frontmatter, encoding="utf-8")

    draft_file.parent.mkdir(parents=True, exist_ok=True)
    draft_file.write_text(frontmatter, encoding="utf-8")

    return SocialDraft(
        platform=clean_platform,
        title=slug,
        text=clean_text,
        image_asset=image_asset_path,
        draft_path=draft_file,
        char_count=len(clean_text),
        status="draft",
        is_thread=is_thread,
        thread_count=len(thread_parts),
    )


async def publish_social_draft(
    draft_file: Path,
    platform: str,
) -> str:
    """Mark a social draft as published. Permission-gated via Set B (requires confirmation)."""
    if not draft_file.is_file():
        raise SocialError(f"Draft file not found: {draft_file}")

    content = draft_file.read_text(encoding="utf-8")
    now_iso = datetime.now(UTC).isoformat()
    updated = content.replace("status: draft", f"status: published\npublished_at: {now_iso}")
    draft_file.write_text(updated, encoding="utf-8")
    return f"Successfully marked {draft_file.name} as published to {platform.upper()}."
