"""Phase 8 tools: FLUX image generation and social post drafting."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from server.image.flux import (
    FluxError,
    FluxImageGenerator,
    FluxRateLimitError,
    ImageResult,
)
from server.social.draft import (
    SocialDraft,
    SocialError,
    create_social_draft,
    publish_social_draft,
)
from server.tools.base import Tool, ToolContext, ToolError

log = logging.getLogger(__name__)


def _get_generator(ctx: ToolContext) -> FluxImageGenerator:
    if ctx.image_gen is not None and isinstance(ctx.image_gen, FluxImageGenerator):
        return ctx.image_gen
    return FluxImageGenerator(
        settings=ctx.settings,
        runner=ctx.runner,
        db=ctx.db,
    )


class GenerateImageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(description="Visual description of the image to generate")
    aspect_ratio: Literal["1:1", "16:9"] = Field(
        default="1:1",
        description="Target aspect ratio: '1:1' for square avatars/art, '16:9' for social cards",
    )
    enhance: bool = Field(
        default=True,
        description="Whether to inject positive photorealistic styling keywords",
    )
    force: bool = Field(
        default=False,
        description="Bypass cached images and force generation",
    )


async def generate_image(args: GenerateImageArgs, ctx: ToolContext) -> str:
    gen = _get_generator(ctx)
    try:
        res: ImageResult = await gen.generate_image(
            prompt=args.prompt,
            aspect_ratio=args.aspect_ratio,
            enhance=args.enhance,
            force=args.force,
        )
    except FluxRateLimitError as e:
        raise ToolError(str(e)) from e
    except FluxError as e:
        raise ToolError(f"Image generation failed: {e}") from e

    status = "Cached" if res.cached else "Generated"
    return (
        f"{status} {res.aspect_ratio} image ({res.resolution}): "
        f"saved to {res.file_path.name}. Vault asset: {res.vault_asset_path}"
    )


class DraftSocialPostArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["x", "twitter", "linkedin"] = Field(
        description="Target social platform: 'x', 'twitter', or 'linkedin'"
    )
    text: str = Field(description="Post content or caption")
    image_prompt: str | None = Field(
        default=None,
        description="Optional prompt to generate an accompanying 16:9 FLUX visual",
    )


async def draft_social_post(args: DraftSocialPostArgs, ctx: ToolContext) -> str:
    gen = _get_generator(ctx)
    try:
        draft: SocialDraft = await create_social_draft(
            settings=ctx.settings,
            db=ctx.db,
            platform=args.platform,
            text=args.text,
            image_prompt=args.image_prompt,
            image_generator=gen,
        )
    except (SocialError, FluxError) as e:
        raise ToolError(f"Social drafting failed: {e}") from e

    visual_note = f" Visual attached: {draft.image_asset}." if draft.image_asset else ""
    thread_note = f" (thread of {draft.thread_count} parts)" if draft.is_thread else ""
    return (
        f"Drafted {draft.platform.upper()} post{thread_note} ({draft.char_count} chars). "
        f"Saved note to {draft.draft_path.name}.{visual_note}"
    )


class PublishSocialPostArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["x", "twitter", "linkedin"] = Field(
        description="Platform to publish: 'x', 'twitter', or 'linkedin'"
    )
    draft_file: str = Field(description="Path or filename of the draft markdown note")


async def publish_social_post(args: PublishSocialPostArgs, ctx: ToolContext) -> str:
    path = Path(args.draft_file).expanduser()
    if not path.is_absolute():
        # Check in Obsidian Vault or data/social/drafts
        candidates: list[Path] = []
        if ctx.settings.vault_path:
            candidates.append(ctx.settings.vault_path / "Social" / "Drafts" / path.name)
        candidates.append(ctx.settings.sqlite_path.parent / "social" / "drafts" / path.name)
        candidates.append(path)
        for c in candidates:
            if c.is_file():
                path = c
                break

    try:
        return await publish_social_draft(path, platform=args.platform)
    except SocialError as e:
        raise ToolError(str(e)) from e


TOOLS = [
    Tool(
        "generate_image",
        "image:generate",
        "Generate an image using FLUX.1-schnell with 1:1 or 16:9 social formatting",
        GenerateImageArgs,
        generate_image,
        cache_ttl_s=None,
    ),
    Tool(
        "draft_social_post",
        "social:draft",
        "Draft a post for X/Twitter or LinkedIn with optional 16:9 FLUX image in Obsidian",
        DraftSocialPostArgs,
        draft_social_post,
        cache_ttl_s=None,
    ),
    Tool(
        "publish_social_post",
        "social:publish_x",
        "Publish or finalize a social media post (requires user confirmation)",
        PublishSocialPostArgs,
        publish_social_post,
        cache_ttl_s=None,
    ),
    Tool(
        "publish_linkedin_post",
        "social:publish_linkedin",
        "Publish or finalize a LinkedIn social post (requires user confirmation)",
        PublishSocialPostArgs,
        publish_social_post,
        cache_ttl_s=None,
    ),
]
