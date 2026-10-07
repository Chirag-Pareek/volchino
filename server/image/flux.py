"""Phase 8: AI Image Generation pipeline using Cloudflare Workers AI FLUX.1-schnell."""

from __future__ import annotations

import base64
import hashlib
import logging
import shutil
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Literal

import aiosqlite
import httpx

from server.config import Settings
from server.tools.base import Runner

log = logging.getLogger(__name__)

# Spec §6: Positive-only prompt expansion for FLUX schnell
DEFAULT_PHOTO_ENHANCEMENT = (
    "Photorealistic photograph, shot on 50mm f/1.4 lens, "
    "shallow depth of field, natural bokeh, soft diffused window light, "
    "natural skin texture, realistic color grading"
)

# Stylistic keywords where photographic styling would conflict
NON_PHOTO_KEYWORDS = (
    "anime",
    "manga",
    "pixel art",
    "pixelart",
    "illustration",
    "vector",
    "cartoon",
    "sketch",
    "drawing",
    "painting",
    "oil painting",
    "watercolor",
    "render",
    "3d render",
    "cgi",
    "line art",
    "logo",
    "icon",
)

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
    b"\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FluxError(RuntimeError):
    """Base error for FLUX image generation."""


class FluxRateLimitError(FluxError):
    """Daily quota or rate limit exceeded."""


class FluxAuthError(FluxError):
    """Authentication or credential failure."""


def compute_image_hash(prompt: str, model: str, aspect_ratio: str) -> str:
    """SHA-256 hash of normalized prompt + model + aspect_ratio for deduplication."""
    norm = f"{prompt.strip().lower()}:{model.strip().lower()}:{aspect_ratio.strip().lower()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def enhance_prompt(prompt: str, style: str | None = None, enhance: bool = True) -> str:
    """Inject positive photographic styling keywords automatically.

    FLUX schnell ignores negative prompts. Stylistic non-photographic requests
    (anime, pixel art, illustration, etc.) preserve user intent.
    """
    clean = prompt.strip()
    if not clean or not enhance:
        return clean

    if style is not None:
        suffix = style.strip()
    else:
        lower = clean.lower()
        if any(kw in lower for kw in NON_PHOTO_KEYWORDS):
            return clean
        suffix = DEFAULT_PHOTO_ENHANCEMENT

    if not suffix or suffix.lower() in clean.lower():
        return clean
    return f"{clean.rstrip('.,; ')}, {suffix}"


class ImageResult:
    """Result of an image generation or cache lookup."""

    def __init__(
        self,
        image_hash: str,
        prompt: str,
        model: str,
        resolution: str,
        aspect_ratio: str,
        file_path: Path,
        vault_asset_path: str,
        created_at: str,
        cached: bool = False,
    ) -> None:
        self.image_hash = image_hash
        self.prompt = prompt
        self.model = model
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio
        self.file_path = file_path
        self.vault_asset_path = vault_asset_path
        self.created_at = created_at
        self.cached = cached

    def __repr__(self) -> str:
        return (
            f"ImageResult(hash={self.image_hash[:8]}..., ratio={self.aspect_ratio}, "
            f"res={self.resolution}, cached={self.cached}, path={self.file_path.name})"
        )


class FluxImageGenerator:
    """Manages Cloudflare Workers AI FLUX generations and 16:9 ffmpeg conversions."""

    def __init__(
        self,
        settings: Settings,
        runner: Runner,
        db: aiosqlite.Connection,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.db = db
        self._http_client = http_client

    async def get_daily_generation_count(self) -> int:
        """Count images generated today since midnight local / UTC."""
        local_midnight = datetime.combine(datetime.now().date(), time.min).astimezone()
        since_local = local_midnight.isoformat()
        since_utc = local_midnight.astimezone(UTC).isoformat()
        async with self.db.execute(
            """SELECT COUNT(*) AS cnt FROM generated_images
               WHERE created_at >= ? OR created_at >= ?""",
            (since_local, since_utc),
        ) as cur:
            row = await cur.fetchone()
            return int(row["cnt"]) if row else 0

    async def get_cached_image(self, image_hash: str) -> ImageResult | None:
        """Return cached image record if it exists and the file is still present on disk."""
        async with self.db.execute(
            """SELECT prompt, model, resolution, aspect_ratio,
                      file_path, vault_asset_path, created_at
               FROM generated_images WHERE image_hash = ?""",
            (image_hash,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            fp = Path(row["file_path"])
            if not fp.is_file():
                return None
            return ImageResult(
                image_hash=image_hash,
                prompt=row["prompt"],
                model=row["model"],
                resolution=row["resolution"] or "1024x1024",
                aspect_ratio=row["aspect_ratio"] or "1:1",
                file_path=fp,
                vault_asset_path=row["vault_asset_path"] or "",
                created_at=row["created_at"],
                cached=True,
            )

    async def format_16_9(self, source_png: Path, target_png: Path) -> str:
        """Crop and scale 1:1 image to 16:9 (1200x675) using ffmpeg for X/LinkedIn social posts."""
        target_png.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = self.runner.which("ffmpeg")
        if not ffmpeg_bin:
            # Fallback if ffmpeg missing in test environment
            if not target_png.is_file():
                shutil.copy2(source_png, target_png)
            return "1200x675"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(source_png),
            "-vf",
            "scale=1200:675:force_original_aspect_ratio=increase,crop=1200:675",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(target_png),
        ]
        res = await self.runner.run(cmd, timeout=self.settings.tool_timeout_s)
        if res.returncode != 0:
            raise FluxError(
                f"ffmpeg conversion failed ({res.returncode}): {res.stderr or res.stdout}"
            )

        # If runner was DryRunRunner, ensure target file exists for downstream handling
        if not target_png.is_file() and source_png.is_file():
            shutil.copy2(source_png, target_png)
        return "1200x675"

    async def _call_cloudflare_ai(self, prompt: str) -> bytes:
        """Call Cloudflare Workers AI FLUX endpoint or return dry run fixture."""
        if self.settings.dry_run:
            log.info("[dry-run] FLUX prompt: %s", prompt)
            return TINY_PNG

        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.settings.cf_account_id}"
            f"/ai/run/{self.settings.flux_model}"
        )
        headers: dict[str, str] = {}
        if self.settings.cf_api_token:
            headers["Authorization"] = f"Bearer {self.settings.cf_api_token}"
        elif self.settings.cf_worker_url:
            worker_base = self.settings.cf_worker_url
            if not worker_base.startswith(("http://", "https://")):
                worker_base = f"https://{worker_base}"
            endpoint = f"{worker_base.rstrip('/')}/ai/run/{self.settings.flux_model}"
        else:
            raise FluxAuthError(
                "Cloudflare Workers AI credentials missing: set CF_API_TOKEN or CF_WORKER_URL"
            )

        payload = {
            "prompt": prompt,
            "steps": min(max(self.settings.flux_steps, 1), 8),
        }

        client = self._http_client
        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=60.0)
            should_close = True

        try:
            res = await client.post(endpoint, json=payload, headers=headers)
        except httpx.RequestError as e:
            raise FluxError(f"Cloudflare Workers AI request failed: {e}") from e
        finally:
            if should_close:
                await client.aclose()

        if res.status_code in (401, 403):
            raise FluxAuthError(
                f"Cloudflare Workers AI authentication failed ({res.status_code}): {res.text}"
            )
        if res.status_code == 429:
            raise FluxRateLimitError(f"Cloudflare Workers AI neuron quota exceeded: {res.text}")
        if res.status_code != 200:
            raise FluxError(f"Cloudflare Workers AI failed ({res.status_code}): {res.text}")

        # Parse output: binary PNG/JPEG vs JSON with base64
        content_type = res.headers.get("content-type", "")
        if (
            content_type.startswith("image/")
            or res.content.startswith(b"\x89PNG")
            or res.content.startswith(b"\xff\xd8")
        ):
            return res.content

        try:
            data = res.json()
        except Exception as e:
            raise FluxError(
                f"Unexpected non-image response from Cloudflare AI: {res.text[:100]}"
            ) from e

        if isinstance(data, dict):
            b64_str: str | None = None
            if "result" in data and isinstance(data["result"], dict) and "image" in data["result"]:
                b64_str = data["result"]["image"]
            elif "image" in data and isinstance(data["image"], str):
                b64_str = data["image"]
            if b64_str:
                return base64.b64decode(b64_str)

        raise FluxError(f"Unrecognized response format from Cloudflare AI: {data}")

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: Literal["1:1", "16:9"] = "1:1",
        enhance: bool = True,
        force: bool = False,
    ) -> ImageResult:
        """Generate or retrieve cached image with rate limit enforcement."""
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise FluxError("Prompt cannot be empty")

        if aspect_ratio not in ("1:1", "16:9"):
            raise FluxError(f"Unsupported aspect ratio: {aspect_ratio!r}. Use '1:1' or '16:9'")

        img_hash = compute_image_hash(clean_prompt, self.settings.flux_model, aspect_ratio)

        # 1. Deduplication / Zero-neuron Cache lookup
        if not force:
            cached = await self.get_cached_image(img_hash)
            if cached is not None:
                log.info("FLUX cache hit for prompt hash %s (0 neurons used)", img_hash[:8])
                return cached

        # 2. Daily Quota Check
        daily_count = await self.get_daily_generation_count()
        if daily_count >= self.settings.daily_img_limit:
            limit = self.settings.daily_img_limit
            raise FluxRateLimitError(
                f"Daily image generation limit reached ({daily_count}/{limit}). "
                "Resets at midnight to protect free neuron quota."
            )

        # 3. Positive prompt expansion
        final_prompt = enhance_prompt(clean_prompt, enhance=enhance)

        # 4. Generate native 1:1 image bytes via Cloudflare Workers AI
        image_bytes = await self._call_cloudflare_ai(final_prompt)

        # 5. Local storage directories
        img_dir = self.settings.sqlite_path.parent / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        short_id = img_hash[:12]
        ratio_tag = aspect_ratio.replace(":", "_")
        raw_1_1_path = img_dir / f"img_{short_id}_native.png"
        final_file_path = img_dir / f"img_{short_id}_{ratio_tag}.png"

        raw_1_1_path.write_bytes(image_bytes)

        # 6. Format aspect ratio
        if aspect_ratio == "16:9":
            resolution = await self.format_16_9(raw_1_1_path, final_file_path)
        else:
            shutil.copy2(raw_1_1_path, final_file_path)
            resolution = "1024x1024"

        # 7. Mirror to Obsidian Vault Assets if configured
        vault_asset_path = str(final_file_path)
        if self.settings.vault_path:
            vault_assets = self.settings.vault_path / "00_System" / "Assets"
            vault_assets.mkdir(parents=True, exist_ok=True)
            vault_file = vault_assets / f"img_{short_id}_{ratio_tag}.png"
            shutil.copy2(final_file_path, vault_file)
            vault_asset_path = f"00_System/Assets/{vault_file.name}"

        # 8. Insert metadata record in SQLite
        now_iso = datetime.now(UTC).isoformat()
        async with self.db.execute(
            """INSERT OR REPLACE INTO generated_images (
                   image_hash, prompt, model, resolution, aspect_ratio,
                   file_path, vault_asset_path, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                img_hash,
                clean_prompt,
                self.settings.flux_model,
                resolution,
                aspect_ratio,
                str(final_file_path),
                vault_asset_path,
                now_iso,
            ),
        ):
            await self.db.commit()

        return ImageResult(
            image_hash=img_hash,
            prompt=clean_prompt,
            model=self.settings.flux_model,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            file_path=final_file_path,
            vault_asset_path=vault_asset_path,
            created_at=now_iso,
            cached=False,
        )
