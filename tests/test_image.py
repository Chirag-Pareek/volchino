"""Tests for Phase 8: FLUX Image Generation, Hash Caching, and Social Drafting."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from server.config import Settings
from server.image.flux import (
    DEFAULT_PHOTO_ENHANCEMENT,
    TINY_PNG,
    FluxAuthError,
    FluxImageGenerator,
    FluxRateLimitError,
    compute_image_hash,
    enhance_prompt,
)
from server.permissions import Decision, check
from server.pipeline.deterministic import match
from server.social.draft import (
    create_social_draft,
    publish_social_draft,
    split_x_thread,
)
from server.tools import REGISTRY
from server.tools.base import DryRunRunner, ToolArgError, ToolContext


@pytest.fixture()
def image_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token="test-secret",
        sqlite_path=tmp_path / "memory.db",
        vault_path=tmp_path / "vault",
        dry_run=True,
        daily_img_limit=5,
        flux_model="@cf/black-forest-labs/flux-1-schnell",
        flux_steps=8,
        cf_account_id="cf-acc-123",
        cf_api_token="cf-tok-abc",
    )


@pytest.fixture()
def image_generator(image_settings: Settings, db, runner: DryRunRunner) -> FluxImageGenerator:
    return FluxImageGenerator(
        settings=image_settings,
        runner=runner,
        db=db,
    )


# ── 1. Hash & Prompt Enhancement ──


def test_compute_image_hash():
    h1 = compute_image_hash("Cyberpunk Cat", "@cf/black-forest-labs/flux-1-schnell", "1:1")
    h2 = compute_image_hash("  cyberpunk cat  ", "@cf/black-forest-labs/flux-1-schnell", "1:1")
    assert h1 == h2
    assert len(h1) == 64

    # Aspect ratio differentiation
    h_16_9 = compute_image_hash("Cyberpunk Cat", "@cf/black-forest-labs/flux-1-schnell", "16:9")
    assert h1 != h_16_9

    # Model differentiation
    h_other_model = compute_image_hash("Cyberpunk Cat", "other-model", "1:1")
    assert h1 != h_other_model


def test_enhance_prompt_photographic():
    prompt = "a coffee cup on a wooden table"
    enhanced = enhance_prompt(prompt)
    assert enhanced.startswith("a coffee cup on a wooden table,")
    assert DEFAULT_PHOTO_ENHANCEMENT in enhanced


def test_enhance_prompt_artistic_preserved():
    # If user specifies non-photo styles (anime, pixel art, etc.), photo style is NOT forced
    anime_prompt = "anime girl in neon cyberpunk tokyo"
    assert enhance_prompt(anime_prompt) == anime_prompt

    pixel_prompt = "pixel art sword icon"
    assert enhance_prompt(pixel_prompt) == pixel_prompt

    sketch_prompt = "pencil sketch of a mountain"
    assert enhance_prompt(sketch_prompt) == sketch_prompt


def test_enhance_prompt_disabled():
    prompt = "portrait of a warrior"
    assert enhance_prompt(prompt, enhance=False) == prompt


def test_enhance_prompt_custom_style():
    prompt = "sunset over ocean"
    custom = enhance_prompt(prompt, style="cinematic golden hour, film grain")
    assert custom == "sunset over ocean, cinematic golden hour, film grain"


# ── 2. Generation, Rate Limits & Hash Caching ──


async def test_generate_image_1_to_1(image_generator: FluxImageGenerator, image_settings: Settings):
    res = await image_generator.generate_image("A cute robot puppy", aspect_ratio="1:1")
    assert res.cached is False
    assert res.aspect_ratio == "1:1"
    assert res.resolution == "1024x1024"
    assert res.file_path.is_file()
    assert res.vault_asset_path.startswith("00_System/Assets/")

    # Check vault asset exists
    vault_file = image_settings.vault_path / res.vault_asset_path
    assert vault_file.is_file()


async def test_cache_deduplication_zero_tokens(image_generator: FluxImageGenerator):
    # 1st call: fresh generation
    res1 = await image_generator.generate_image("Neon cyber city", aspect_ratio="1:1")
    assert res1.cached is False

    # 2nd call: cached return with zero neuron cost
    res2 = await image_generator.generate_image("neon cyber city", aspect_ratio="1:1")
    assert res2.cached is True
    assert res2.image_hash == res1.image_hash
    assert res2.file_path == res1.file_path

    # Force generation bypasses cache
    res3 = await image_generator.generate_image("neon cyber city", aspect_ratio="1:1", force=True)
    assert res3.cached is False


async def test_daily_rate_limit_enforced(image_generator: FluxImageGenerator):
    # Generate up to limit (5)
    for i in range(5):
        res = await image_generator.generate_image(f"Unique scene number {i}", aspect_ratio="1:1")
        assert res.cached is False

    # 6th generation must raise rate limit error
    with pytest.raises(FluxRateLimitError, match="Daily image generation limit reached"):
        await image_generator.generate_image("Unique scene number 6", aspect_ratio="1:1")


async def test_cached_lookup_bypasses_rate_limit(image_generator: FluxImageGenerator):
    # Prime 5 images to fill daily quota
    for i in range(5):
        await image_generator.generate_image(f"Quota test prompt {i}", aspect_ratio="1:1")

    # Re-requesting an already generated image should succeed via cache (0 cost)
    res = await image_generator.generate_image("Quota test prompt 0", aspect_ratio="1:1")
    assert res.cached is True


# ── 3. Aspect Ratio 16:9 & Ffmpeg Formatting ──


async def test_generate_image_16_to_9_ffmpeg(
    image_generator: FluxImageGenerator, runner: DryRunRunner
):
    res = await image_generator.generate_image("Scenic mountain ridge", aspect_ratio="16:9")
    assert res.aspect_ratio == "16:9"
    assert res.resolution == "1200x675"
    assert res.file_path.is_file()

    # Verify ffmpeg was invoked with correct 16:9 scale/crop filter
    ffmpeg_calls = [c for c in runner.calls if "ffmpeg" in c[0]]
    assert len(ffmpeg_calls) >= 1
    args_str = " ".join(ffmpeg_calls[0])
    assert "scale=1200:675:force_original_aspect_ratio=increase,crop=1200:675" in args_str


# ── 4. Cloudflare Response Handling ──


async def test_cloudflare_api_mock_responses(image_settings: Settings, db, runner: DryRunRunner):
    # Test JSON base64 format response
    fake_b64 = base64.b64encode(TINY_PNG).decode("ascii")
    mock_response = httpx.Response(
        status_code=200,
        json={"result": {"image": fake_b64}},
        headers={"content-type": "application/json"},
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    real_settings = Settings(
        sqlite_path=image_settings.sqlite_path,
        vault_path=image_settings.vault_path,
        dry_run=False,
        cf_account_id="acc123",
        cf_api_token="tok123",
    )
    gen = FluxImageGenerator(settings=real_settings, runner=runner, db=db, http_client=mock_client)
    res = await gen.generate_image("Sunset on Mars")
    assert res.file_path.is_file()
    assert res.file_path.read_bytes() == TINY_PNG


async def test_cloudflare_api_errors(image_settings: Settings, db, runner: DryRunRunner):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = httpx.Response(status_code=401, text="Unauthorized")

    real_settings = Settings(
        sqlite_path=image_settings.sqlite_path,
        dry_run=False,
        cf_account_id="acc123",
        cf_api_token="bad-token",
    )
    gen = FluxImageGenerator(settings=real_settings, runner=runner, db=db, http_client=mock_client)

    with pytest.raises(FluxAuthError):
        await gen.generate_image("Test auth error")


# ── 5. Social Drafting & Thread Splitting ──


def test_split_x_thread():
    short_text = "Just launched Phase 8 for Volchino! #AI #Linux"
    assert split_x_thread(short_text) == [short_text]

    long_text = " ".join(["word" + str(i) for i in range(100)])
    parts = split_x_thread(long_text, limit=100)
    assert len(parts) > 1
    assert all("(1/" in parts[0] or f"/{len(parts)})" in p for p in parts)


async def test_create_social_draft_x(
    image_settings: Settings, db, image_generator: FluxImageGenerator
):
    draft = await create_social_draft(
        settings=image_settings,
        db=db,
        platform="x",
        text="Excited to share the architecture of our personal AI agent on Arch Linux!",
        image_prompt="Modern Arch Linux desktop setup with glowing monitors",
        image_generator=image_generator,
    )
    assert draft.platform == "x"
    assert draft.is_thread is False
    assert draft.draft_path.is_file()
    assert draft.image_asset is not None
    assert "00_System/Assets/" in draft.image_asset

    content = draft.draft_path.read_text(encoding="utf-8")
    assert "status: draft" in content
    assert "Attached Visual (16:9 Social Card)" in content


async def test_publish_social_draft(tmp_path: Path):
    draft_file = tmp_path / "draft.md"
    draft_file.write_text("---\nstatus: draft\n---\nPost content", encoding="utf-8")

    res = await publish_social_draft(draft_file, platform="linkedin")
    assert "Successfully marked" in res
    assert "status: published" in draft_file.read_text(encoding="utf-8")


# ── 6. Tool Validation & Execution via REGISTRY ──


async def test_tool_generate_image_execution(ctx: ToolContext, image_generator: FluxImageGenerator):
    ctx.image_gen = image_generator
    tool = REGISTRY["generate_image"]

    # Valid execution
    out = await tool(
        {"prompt": "A friendly cat sitting by a fireplace", "aspect_ratio": "1:1"},
        ctx,
    )
    assert "image (1024x1024)" in out

    # Invalid aspect ratio
    with pytest.raises(ToolArgError):
        await tool({"prompt": "cat", "aspect_ratio": "4:3"}, ctx)

    # Missing prompt
    with pytest.raises(ToolArgError):
        await tool({}, ctx)


async def test_tool_draft_social_post_execution(
    ctx: ToolContext, image_generator: FluxImageGenerator
):
    ctx.image_gen = image_generator
    tool = REGISTRY["draft_social_post"]

    out = await tool(
        {
            "platform": "linkedin",
            "text": "Productivity skyrocketed after building a local AI daemon on Hyprland.",
            "image_prompt": "Futuristic developer workspace",
        },
        ctx,
    )
    assert "Drafted LINKEDIN post" in out


# ── 7. Permissions (Set A vs Set B) ──


def test_permissions_phase_8():
    assert check("image:generate") is Decision.ALLOW
    assert check("social:draft") is Decision.ALLOW
    assert check("social:publish_x") is Decision.CONFIRM
    assert check("social:publish_linkedin") is Decision.CONFIRM


# ── 8. Deterministic Regex Matching ──


def test_deterministic_matching_image():
    call1 = match("generate an image of a cybernetic cat")
    assert call1 is not None
    assert call1.tool == "generate_image"
    assert call1.args["prompt"] == "a cybernetic cat"
    assert call1.args["aspect_ratio"] == "1:1"

    call2 = match("create image of sunset over the ocean in 16:9")
    assert call2 is not None
    assert call2.tool == "generate_image"
    assert call2.args["prompt"] == "sunset over the ocean"
    assert call2.args["aspect_ratio"] == "16:9"


def test_deterministic_matching_social():
    call1 = match("draft tweet: shipping phase 8 today!")
    assert call1 is not None
    assert call1.tool == "draft_social_post"
    assert call1.args["platform"] == "x"
    assert call1.args["text"] == "shipping phase 8 today!"

    call2 = match("draft linkedin post: engineering update on volchino agent")
    assert call2 is not None
    assert call2.tool == "draft_social_post"
    assert call2.args["platform"] == "linkedin"
    assert call2.args["text"] == "engineering update on volchino agent"
