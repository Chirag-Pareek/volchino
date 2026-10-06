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
        return cls(
            server_host=os.environ.get("SERVER_HOST", LOOPBACK_HOST).strip() or LOOPBACK_HOST,
            server_port=_int("SERVER_PORT", 8765),
            auth_token=os.environ.get("AUTH_TOKEN", "").strip(),
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
            groq_model=os.environ.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
            opencode_api_key=os.environ.get("OPENCODE_API_KEY", "").strip(),
            sqlite_path=sqlite_path,
            vault_path=Path(vault_raw).expanduser() if vault_raw else None,
            default_workspace=_int("DEFAULT_WORKSPACE", 1),
            dry_run=_bool("VOLCHINO_DRY_RUN"),
            stt_engine=os.environ.get("STT_ENGINE", "").strip() or "faster-whisper",
            stt_model=os.environ.get("STT_MODEL", "").strip() or "base.en",
            tts_engine=os.environ.get("TTS_ENGINE", "").strip() or "edge-tts",
            tts_voice=os.environ.get("TTS_VOICE", "").strip() or "en-US-ChristopherNeural",
            wake_word=os.environ.get("WAKE_WORD", "").strip() or "wake up",
        )
