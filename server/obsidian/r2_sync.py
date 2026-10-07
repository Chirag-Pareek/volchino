"""Cloudflare R2 async backup bridge for Obsidian Vault markdown notes."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from server.config import Settings

log = logging.getLogger(__name__)

# Extensions that must NEVER be synced to R2
BLACKLISTED_SUFFIXES = frozenset(
    {
        ".db",
        ".db-wal",
        ".db-shm",
        ".sqlite",
        ".sqlite3",
        ".db3",
        ".sqlite-wal",
        ".sqlite-shm",
    }
)

# Allowed file extensions for vault sync
ALLOWED_VAULT_SUFFIXES = frozenset({".md", ".canvas", ".txt"})


def is_syncable_vault_file(path: Path) -> bool:
    """Return True if path is a safe Markdown/text note and strictly NOT a database file."""
    name_lower = path.name.lower()
    suffix_lower = path.suffix.lower()

    # Absolute blacklist check: NEVER sync SQLite database files
    if suffix_lower in BLACKLISTED_SUFFIXES or ".db" in name_lower or "memory.db" in name_lower:
        return False

    # Skip VCS / temporary directories
    for part in path.parts:
        if part in {".git", ".trash", ".venv", "__pycache__", ".pytest_cache"}:
            return False

    # Sync only allowed vault note types
    return suffix_lower in ALLOWED_VAULT_SUFFIXES


def calculate_file_hash(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class R2SyncBridge:
    """Async background bridge syncing $VAULT_PATH markdown files to Cloudflare R2."""

    def __init__(
        self,
        settings: Settings,
        vault_path: Path | None = None,
        bucket: str | None = None,
        interval_s: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.vault_path = (
            vault_path
            or settings.vault_path
            or (Path.home() / "Documents" / "Obsidian Vault")
        )
        self.bucket = bucket or settings.cf_r2_bucket or "obsidian-vault"
        self.account_id = settings.cf_account_id
        self.api_token = settings.cf_api_token
        self.access_key_id = settings.r2_access_key_id
        self.secret_access_key = settings.r2_secret_access_key
        self.interval_s = interval_s or settings.r2_sync_interval_s or 300.0

        self.initial_backoff_s: float = 10.0
        self.max_backoff_s: float = 300.0
        self._consecutive_failures: int = 0
        self._file_cache: dict[str, tuple[float, str]] = {}  # rel_path -> (mtime, sha256)

        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = client

    def get_synced_files(self) -> list[Path]:
        """Scan vault directory for valid syncable files, strictly excluding any database files."""
        if not self.vault_path or not self.vault_path.is_dir():
            return []
        files: list[Path] = []
        for p in self.vault_path.rglob("*"):
            if p.is_file() and is_syncable_vault_file(p):
                files.append(p)
        return sorted(files)

    def _sign_s3_v4(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: bytes,
    ) -> dict[str, str]:
        """Compute minimal AWS SigV4 headers for Cloudflare R2 S3 endpoint."""
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(payload).hexdigest()
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = payload_hash

        parsed = httpx.URL(url)
        host = parsed.netloc
        canonical_uri = quote(parsed.path, safe="/-_.~")
        canonical_querystring = parsed.query.decode("utf-8") if parsed.query else ""

        canonical_headers = (
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"

        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/auto/s3/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, "auto")
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={self.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers["Authorization"] = auth_header
        return headers

    async def _upload_file(self, client: httpx.AsyncClient, rel_path: str, data: bytes) -> bool:
        """Upload a single file to Cloudflare R2."""
        # 1. S3-compatible endpoint (if keys configured)
        if self.account_id and self.access_key_id and self.secret_access_key:
            key_quoted = quote(rel_path, safe="/-_.~")
            url = f"https://{self.account_id}.r2.cloudflarestorage.com/{self.bucket}/{key_quoted}"
            headers = {"content-type": "text/markdown"}
            headers = self._sign_s3_v4("PUT", url, headers, data)
            resp = await client.put(url, headers=headers, content=data)
            return resp.is_success

        # 2. Direct Cloudflare API token endpoint
        if self.account_id and self.api_token:
            url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/r2/buckets/{self.bucket}/objects/{quote(rel_path, safe='/-_.~')}"
            )
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "content-type": "text/markdown",
            }
            resp = await client.put(url, headers=headers, content=data)
            return resp.is_success

        # No remote credentials configured: record as simulated/dry sync
        log.debug("No R2 credentials configured; mock sync accepted for %s", rel_path)
        return True

    async def sync_once(self) -> tuple[int, int]:
        """Perform a single sync cycle. Returns (synced_count, skipped_count)."""
        files = self.get_synced_files()
        if not files:
            log.debug("No syncable files found in %s", self.vault_path)
            return 0, 0

        synced = 0
        skipped = 0

        # Create or use HTTP client
        client_to_close = None
        if self._client is not None:
            client = self._client
        else:
            client = httpx.AsyncClient(timeout=15.0)
            client_to_close = client

        try:
            for path in files:
                rel_path = str(path.relative_to(self.vault_path))
                mtime = path.stat().st_mtime

                # Check cache: skip if mtime unchanged
                cached = self._file_cache.get(rel_path)
                if cached and cached[0] == mtime:
                    skipped += 1
                    continue

                content = path.read_bytes()
                f_hash = hashlib.sha256(content).hexdigest()

                if cached and cached[1] == f_hash:
                    self._file_cache[rel_path] = (mtime, f_hash)
                    skipped += 1
                    continue

                # Upload file
                success = await self._upload_file(client, rel_path, content)
                if success:
                    self._file_cache[rel_path] = (mtime, f_hash)
                    synced += 1
                else:
                    log.warning("R2 sync upload failed for %s", rel_path)

            self._consecutive_failures = 0
            if synced > 0:
                log.info("R2 sync complete: uploaded %d notes (skipped %d)", synced, skipped)
            return synced, skipped

        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, OSError) as e:
            self._consecutive_failures += 1
            backoff = min(
                self.max_backoff_s,
                self.initial_backoff_s * (2 ** (self._consecutive_failures - 1)),
            )
            log.warning(
                "R2 sync failed (network offline / unreachable): %s. Backing off for %.1fs",
                e,
                backoff,
            )
            return synced, skipped
        finally:
            if client_to_close:
                await client_to_close.aclose()

    async def _loop(self) -> None:
        """Periodic sync loop with exponential backoff on failure."""
        log.info(
            "R2 sync bridge started (sync interval=%.1fs, bucket=%s)",
            self.interval_s,
            self.bucket,
        )
        while self._running:
            try:
                await self.sync_once()
            except Exception:
                log.exception("Unexpected error during R2 sync cycle")

            # Determine sleep time based on failure count
            if self._consecutive_failures > 0:
                sleep_s = min(
                    self.max_backoff_s,
                    self.initial_backoff_s * (2 ** (self._consecutive_failures - 1)),
                )
            else:
                sleep_s = self.interval_s

            try:
                await asyncio.sleep(sleep_s)
            except asyncio.CancelledError:
                break

    async def start(self) -> None:
        """Start the background sync loop task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="r2_sync_bridge_loop")

    async def stop(self) -> None:
        """Stop the sync bridge task."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("R2 sync bridge stopped")
