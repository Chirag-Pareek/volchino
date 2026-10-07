"""Settings loaded from the process environment (optionally seeded from a dotenv file).

Secret values are never logged or included in ``repr``. Key names mirror ``.env.example``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOOPBACK_HOST = "127.0.0.1"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        log.warning("Invalid integer for %s; using default %s", name, default)
        return default


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    server_host: str = LOOPBACK_HOST
    server_port: int = 8765
    auth_token: str = field(default="", repr=False)
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    opencode_api_key: str = field(default="", repr=False)
    sqlite_path: Path = PROJECT_ROOT / "data" / "memory.db"
    vault_path: Path | None = None
    default_workspace: int = 1
    dry_run: bool = False
    web_dir: Path = PROJECT_ROOT / "web"
    default_repo: Path = PROJECT_ROOT
    screenshot_dir: Path = Path.home() / "Pictures" / "Screenshots"
    power_supply_dir: Path = Path("/sys/class/power_supply")
    pet_success_timeout_s: float = 3.0
    pet_sleep_after_s: float = 30 * 60
    tool_timeout_s: float = 10.0
    # Phase 4 — voice
    stt_engine: str = "faster-whisper"
    stt_model: str = "base.en"
    tts_engine: str = "edge-tts"
    tts_voice: str = "en-US-ChristopherNeural"
    wake_word: str = "wake up"
    # Phase 6 — Activity tracker & Obsidian R2 sync
    cf_account_id: str = field(default="", repr=False)
    cf_r2_bucket: str = "obsidian-vault"
    cf_worker_url: str = ""
    cf_api_token: str = field(default="", repr=False)
    r2_access_key_id: str = field(default="", repr=False)
    r2_secret_access_key: str = field(default="", repr=False)
    r2_sync_interval_s: float = 300.0
    activity_batch_interval_s: float = 60.0
    activity_idle_threshold_s: float = 180.0
    projects_dir: Path = Path("/home/volchino/Allprojects")
    # Phase 7 — Android / ADB
    adb_device_id: str = ""
    adb_port: int = 5555
    adb_auto_reconnect: bool = True

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = None) -> Settings:
        """Build settings from ``os.environ``.

        ``env_file`` (or ``$VOLCHINO_ENV_FILE``, default ``<root>/.env``) is loaded with
        ``override=False`` so explicit process env always wins. Its contents are never logged.
        """
        path = Path(env_file or os.environ.get("VOLCHINO_ENV_FILE") or PROJECT_ROOT / ".env")
        if path.is_file():
            load_dotenv(path, override=False)

        sqlite_raw = os.environ.get("SQLITE_PATH", "").strip() or "./data/memory.db"
        sqlite_path = Path(sqlite_raw).expanduser()
        if not sqlite_path.is_absolute():
            sqlite_path = PROJECT_ROOT / sqlite_path

        vault_raw = os.environ.get("VAULT_PATH", "").strip()
        vault_path: Path | None = None
        if vault_raw:
            vault_path = Path(vault_raw).expanduser()
        elif (Path.home() / "Documents" / "Obsidian Vault").is_dir():
            vault_path = Path.home() / "Documents" / "Obsidian Vault"

        auto_reconn = os.environ.get("ADB_AUTO_RECONNECT", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        projects_dir_raw = (
            os.environ.get("PROJECTS_DIR", "").strip() or "/home/volchino/Allprojects"
        )
        return cls(
            server_host=os.environ.get("SERVER_HOST", LOOPBACK_HOST).strip() or LOOPBACK_HOST,
            server_port=_int("SERVER_PORT", 8765),
            auth_token=os.environ.get("AUTH_TOKEN", "").strip(),
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
            groq_model=os.environ.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
            opencode_api_key=os.environ.get("OPENCODE_API_KEY", "").strip(),
            sqlite_path=sqlite_path,
            vault_path=vault_path,
            default_workspace=_int("DEFAULT_WORKSPACE", 1),
            dry_run=_bool("VOLCHINO_DRY_RUN"),
            stt_engine=os.environ.get("STT_ENGINE", "").strip() or "faster-whisper",
            stt_model=os.environ.get("STT_MODEL", "").strip() or "base.en",
            tts_engine=os.environ.get("TTS_ENGINE", "").strip() or "edge-tts",
            tts_voice=os.environ.get("TTS_VOICE", "").strip() or "en-US-ChristopherNeural",
            wake_word=os.environ.get("WAKE_WORD", "").strip() or "wake up",
            cf_account_id=os.environ.get("CF_ACCOUNT_ID", "").strip(),
            cf_r2_bucket=os.environ.get("CF_R2_BUCKET", "").strip() or "obsidian-vault",
            cf_worker_url=os.environ.get("CF_WORKER_URL", "").strip(),
            cf_api_token=os.environ.get("CF_API_TOKEN", "").strip(),
            r2_access_key_id=(
                os.environ.get("R2_ACCESS_KEY_ID", "").strip()
                or os.environ.get("CF_R2_ACCESS_KEY_ID", "").strip()
            ),
            r2_secret_access_key=(
                os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
                or os.environ.get("CF_R2_SECRET_ACCESS_KEY", "").strip()
            ),
            r2_sync_interval_s=float(_int("R2_SYNC_INTERVAL_S", 300)),
            activity_batch_interval_s=float(_int("ACTIVITY_BATCH_INTERVAL_S", 60)),
            activity_idle_threshold_s=float(_int("ACTIVITY_IDLE_THRESHOLD_S", 180)),
            projects_dir=Path(projects_dir_raw).expanduser(),
            adb_device_id=os.environ.get("ADB_DEVICE_ID", "").strip(),
            adb_port=_int("ADB_PORT", 5555),
            adb_auto_reconnect=auto_reconn,
        )
