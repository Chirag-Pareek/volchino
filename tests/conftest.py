"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from server.config import Settings
from server.db import connect
from server.tools.base import DryRunRunner, ToolContext


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token="test-secret",
        sqlite_path=tmp_path / "test.db",
        dry_run=True,
        web_dir=Path(__file__).resolve().parent.parent / "web",
        default_repo=tmp_path,
        screenshot_dir=tmp_path / "screenshots",
        power_supply_dir=tmp_path / "power",
        pet_success_timeout_s=0.05,
        pet_sleep_after_s=600,
        tool_timeout_s=5.0,
    )


@pytest_asyncio.fixture()
async def db(settings: Settings):
    conn = await connect(settings.sqlite_path)
    yield conn
    await conn.close()


@pytest.fixture()
def runner() -> DryRunRunner:
    return DryRunRunner()


@pytest.fixture()
def ctx(settings: Settings, db, runner: DryRunRunner) -> ToolContext:
    return ToolContext(settings=settings, runner=runner, db=db)
