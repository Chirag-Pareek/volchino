"""Tests for Phase 6: Hyprland Activity Tracker and Obsidian Vault Daily Reports."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from server import db
from server.config import Settings
from server.obsidian.r2_sync import R2SyncBridge, is_syncable_vault_file
from server.obsidian.report_writer import (
    aggregate_activity_for_day,
    build_markdown_report,
    update_project_note,
    write_daily_report,
)
from server.pipeline import deterministic
from server.telemetry.hyprland_ipc import (
    parse_activewindow_data,
    parse_event_line,
    stream_socket_events,
)
from server.telemetry.idle import IdleDetector
from server.telemetry.projects import detect_project, normalize_app_name
from server.telemetry.tracker import ActivityTracker
from server.tools.base import NoArgs, ToolContext
from server.tools.memory_tools import get_work_time_today


class TestHyprlandIpc:
    def test_parse_event_line(self):
        assert parse_event_line("activewindow>>code,main.py - VoLchiNo") == (
            "activewindow",
            "code,main.py - VoLchiNo",
        )
        assert parse_event_line("workspace>>2") == ("workspace", "2")
        assert parse_event_line("focusedmon>>eDP-1,1") == ("focusedmon", "eDP-1,1")
        assert parse_event_line("invalid line without delimiter") is None
        assert parse_event_line("") is None

    def test_parse_activewindow_data(self):
        klass, title = parse_activewindow_data("code,main.py - VoLchiNo - Visual Studio Code")
        assert klass == "code"
        assert title == "main.py - VoLchiNo - Visual Studio Code"

        # Window title with commas
        klass, title = parse_activewindow_data("firefox,YouTube, Videos, and More")
        assert klass == "firefox"
        assert title == "YouTube, Videos, and More"

        # Empty / no window focused
        assert parse_activewindow_data(",") == ("", "")
        assert parse_activewindow_data("") == ("", "")

    async def test_stream_socket_events(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"workspace>>2\nactivewindow>>kitty,fish\n\n")
        reader.feed_eof()

        events = [ev async for ev in stream_socket_events(reader)]
        assert events == [("workspace", "2"), ("activewindow", "kitty,fish")]


class TestProjectsAndAppNormalization:
    def test_normalize_app_name(self):
        assert normalize_app_name("code") == "Code"
        assert normalize_app_name("code-oss") == "Code"
        assert normalize_app_name("vscodium") == "Code"
        assert normalize_app_name("kitty") == "Kitty"
        assert normalize_app_name("firefox") == "Firefox"
        assert normalize_app_name("app.zen_browser.zen") == "Firefox"
        assert normalize_app_name("obsidian") == "Obsidian"
        assert normalize_app_name("antigravity") == "Antigravity"
        assert normalize_app_name("spotify") == "Spotify"
        assert normalize_app_name("") == "Unknown"
        assert normalize_app_name(None) == "Unknown"

    def test_detect_project(self):
        known = ["VoLchiNo", "habit-tracker", "Opencode"]

        # 1. VS Code window title
        title1 = "main.py - VoLchiNo - Visual Studio Code"
        assert detect_project(title1, "Code", known_projects=known) == "VoLchiNo"

        # 2. Path in title
        title2 = "fish: /home/volchino/Allprojects/habit-tracker"
        assert detect_project(title2, "Kitty", known_projects=known) == "habit-tracker"

        # 3. Substring match
        title3 = "[Opencode] Refactoring router"
        assert detect_project(title3, "Firefox", known_projects=known) == "Opencode"

        # 4. Unknown
        assert detect_project("Untitled - Notepad", "Gedit", known_projects=known) is None
        assert detect_project(None, "Code", known_projects=known) is None


class TestIdleDetector:
    async def test_override_idle(self):
        detector = IdleDetector(idle_threshold_s=180.0, enable_input_listener=False)
        assert await detector.is_idle() is False

        detector.set_override_idle(True)
        assert await detector.is_idle() is True

        detector.set_override_idle(False)
        assert await detector.is_idle() is False
        detector.close()

    async def test_input_silence_logic(self):
        detector = IdleDetector(idle_threshold_s=0.05, enable_input_listener=False)
        detector.record_activity()
        assert await detector.is_idle() is False

        await asyncio.sleep(0.06)
        assert await detector.is_idle() is True

        detector.record_activity()
        assert await detector.is_idle() is False
        detector.close()


class TestActivityTracker:
    async def test_window_tracking_and_flush(self, tmp_path: Path):
        db_path = tmp_path / "test_memory.db"
        database = await db.connect(db_path)
        settings = Settings(sqlite_path=db_path, projects_dir=tmp_path / "projects")

        detector = IdleDetector(idle_threshold_s=180.0, enable_input_listener=False)
        tracker = ActivityTracker(
            database,
            settings,
            idle_detector=detector,
            batch_interval_s=60.0,
            tick_interval_s=0.1,
        )

        # 1. Switch to Code on workspace 1
        await tracker.on_workspace_changed("1")
        await tracker.on_window_changed("code", "main.py - VoLchiNo - Visual Studio Code")

        # Accumulate 1.2s active time
        await tracker.tick(0.6)
        await tracker.tick(0.6)

        # 2. Switch to Kitty on workspace 2
        await tracker.on_workspace_changed("2")
        await tracker.on_window_changed("kitty", "fish")

        # Accumulate 1.0s active time
        await tracker.tick(1.0)

        # Flush to SQLite
        count = await tracker.flush()
        assert count >= 1

        # Query activity table
        async with database.execute("SELECT * FROM activity ORDER BY id ASC") as cur:
            rows = await cur.fetchall()

        assert len(rows) >= 1
        first = rows[0]
        assert first["app_name"] == "Code"
        assert first["workspace"] == "1"
        assert first["duration_seconds"] >= 1
        assert first["idle_seconds"] == 0

        detector.close()
        await database.close()

    async def test_idle_time_accumulation(self, tmp_path: Path):
        db_path = tmp_path / "test_memory.db"
        database = await db.connect(db_path)
        settings = Settings(sqlite_path=db_path)

        detector = IdleDetector(idle_threshold_s=180.0, enable_input_listener=False)
        tracker = ActivityTracker(
            database,
            settings,
            idle_detector=detector,
            batch_interval_s=60.0,
            tick_interval_s=0.1,
        )

        await tracker.on_window_changed("firefox", "GitHub")

        # 3 seconds active
        detector.set_override_idle(False)
        for _ in range(3):
            await tracker.tick(1.0)

        # 2 seconds idle
        detector.set_override_idle(True)
        for _ in range(2):
            await tracker.tick(1.0)

        await tracker.flush()

        async with database.execute("SELECT * FROM activity WHERE app_name = 'Firefox'") as cur:
            row = await cur.fetchone()

        assert row is not None
        assert row["duration_seconds"] >= 5
        assert row["idle_seconds"] >= 2

        detector.close()
        await database.close()


class TestReportWriter:
    async def test_aggregate_and_markdown_report(self, tmp_path: Path):
        db_path = tmp_path / "test_memory.db"
        database = await db.connect(db_path)
        today = date.today()
        now_iso = datetime.now().astimezone().isoformat()

        # Seed activity records following schema §9.4
        records = [
            ("Code", "main.py - VoLchiNo", "1", 3600, 600, "VoLchiNo", now_iso),  # 3000s active
            ("Kitty", "fish", "1", 1800, 0, "VoLchiNo", now_iso),  # 1800s active
            ("Firefox", "GitHub", "2", 1200, 0, None, now_iso),  # 1200s active
            ("Obsidian", "Daily.md", "3", 600, 0, None, now_iso),  # 600s active
        ]
        await database.executemany(
            """INSERT INTO activity
               (app_name, window_title, workspace,
                duration_seconds, idle_seconds, project_tag, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        await database.commit()

        # Aggregate metrics
        metrics = await aggregate_activity_for_day(database, today)
        assert metrics.total_duration_seconds == 7200
        assert metrics.total_idle_seconds == 600
        assert metrics.total_active_seconds == 6600  # 1h 50m
        assert metrics.deep_work_seconds == 5400  # Code + Kitty + Obsidian = 3000 + 1800 + 600
        assert metrics.app_durations["Code"] == 3000
        assert metrics.app_durations["Kitty"] == 1800
        assert metrics.app_durations["Firefox"] == 1200
        assert metrics.app_durations["Obsidian"] == 600
        assert metrics.project_durations["VoLchiNo"] == 4800

        # Build markdown and verify sections
        md = build_markdown_report(metrics)
        assert f"# 📊 Daily Activity Report — {today.isoformat()}" in md
        assert "1h 50m" in md
        assert "Deep Work" in md
        assert "Code" in md
        assert "Kitty" in md
        assert "Firefox" in md
        assert "Obsidian" in md
        assert "[[Projects/VoLchiNo|VoLchiNo]]" in md

        # Test write_daily_report creating files in vault
        vault_dir = tmp_path / "Obsidian Vault"
        settings = Settings(sqlite_path=db_path, vault_path=vault_dir)

        report_path, summary = await write_daily_report(database, settings, target_date=today)
        assert report_path.is_file()
        assert report_path == vault_dir / "Daily" / f"{today.isoformat()}.md"
        assert "1h 50m active time" in summary

        # Verify project note was created
        proj_note = vault_dir / "Projects" / "VoLchiNo.md"
        assert proj_note.is_file()
        proj_content = proj_note.read_text(encoding="utf-8")
        assert "## ⏱️ Activity Log" in proj_content
        assert f"- **{today.isoformat()}**:" in proj_content

        # Update note again and verify it modifies the existing line without duplication
        update_project_note(vault_dir, "VoLchiNo", today, 5000)
        updated_content = proj_note.read_text(encoding="utf-8")
        assert updated_content.count(f"- **{today.isoformat()}**:") == 1

        await database.close()


class TestGetWorkTimeTodayDeterministicTool:
    async def test_get_work_time_today_returns_computed_time(self, tmp_path: Path):
        db_path = tmp_path / "test_memory.db"
        database = await db.connect(db_path)
        settings = Settings(sqlite_path=db_path)
        ctx = ToolContext(settings=settings, runner=MagicMock(), db=database)

        # 1. Empty database
        res_empty = await get_work_time_today(NoArgs(), ctx)
        assert res_empty == "Work time today: 0h 0m (no activity tracked yet)."

        # 2. Add activity records for today
        now_iso = datetime.now().astimezone().isoformat()
        records = [
            ("Code", "main.py", "1", 3600, 600, "VoLchiNo", now_iso),  # 3000s active = 50m
            ("Kitty", "terminal", "1", 1800, 0, "VoLchiNo", now_iso),  # 1800s active = 30m
        ]
        await database.executemany(
            """INSERT INTO activity
               (app_name, window_title, workspace,
                duration_seconds, idle_seconds, project_tag, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        await database.commit()

        # 3. Query work time today
        res = await get_work_time_today(NoArgs(), ctx)
        # Total active = 4800s = 1h 20m
        assert "Work time today: 1h 20m" in res
        assert "Code 50m" in res
        assert "Kitty 30m" in res

        await database.close()


class TestR2SyncBridge:
    def test_never_sync_sqlite_db_files(self, tmp_path: Path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()

        # Valid markdown notes
        (vault_dir / "Note1.md").write_text("# Note 1", encoding="utf-8")
        (vault_dir / "Canvas.canvas").write_text("{}", encoding="utf-8")

        # Database files that must NEVER be synced
        (vault_dir / "memory.db").write_bytes(b"SQLITE_DATA")
        (vault_dir / "cache.sqlite").write_bytes(b"SQLITE_DATA")
        (vault_dir / "activity.db-wal").write_bytes(b"WAL")
        (vault_dir / "activity.db-shm").write_bytes(b"SHM")

        # Excluded directory
        git_dir = vault_dir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config", encoding="utf-8")

        # Verify filter function
        assert is_syncable_vault_file(vault_dir / "Note1.md") is True
        assert is_syncable_vault_file(vault_dir / "Canvas.canvas") is True
        assert is_syncable_vault_file(vault_dir / "memory.db") is False
        assert is_syncable_vault_file(vault_dir / "cache.sqlite") is False
        assert is_syncable_vault_file(vault_dir / "activity.db-wal") is False
        assert is_syncable_vault_file(vault_dir / "activity.db-shm") is False
        assert is_syncable_vault_file(git_dir / "config") is False

        # Verify bridge scans only markdown files
        bridge = R2SyncBridge(Settings(), vault_path=vault_dir)
        synced_files = bridge.get_synced_files()
        assert len(synced_files) == 2
        file_names = {p.name for p in synced_files}
        assert file_names == {"Note1.md", "Canvas.canvas"}

    async def test_offline_resilience_and_backoff(self, tmp_path: Path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "Test.md").write_text("# Test Note", encoding="utf-8")

        # Mock client raising network connection error
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.put.side_effect = httpx.ConnectError("Network offline")

        settings = Settings(cf_account_id="fake_acc", cf_api_token="fake_token")
        bridge = R2SyncBridge(settings, vault_path=vault_dir, client=mock_client)

        assert bridge._consecutive_failures == 0
        synced, _ = await bridge.sync_once()

        # Handled gracefully without raising unhandled exception
        assert synced == 0
        assert bridge._consecutive_failures == 1

        # Second failure increases consecutive failures
        await bridge.sync_once()
        assert bridge._consecutive_failures == 2

    async def test_successful_sync_and_caching(self, tmp_path: Path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "Daily.md").write_text("# Daily Note", encoding="utf-8")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_client.put.return_value = mock_resp

        settings = Settings(cf_account_id="fake_acc", cf_api_token="fake_token")
        bridge = R2SyncBridge(settings, vault_path=vault_dir, client=mock_client)

        # First sync uploads
        synced, skipped = await bridge.sync_once()
        assert synced == 1
        assert skipped == 0
        assert bridge._consecutive_failures == 0

        # Second sync skips unchanged file
        synced2, skipped2 = await bridge.sync_once()
        assert synced2 == 0
        assert skipped2 == 1


class TestDeterministicPipeline:
    def test_daily_report_rule_matching(self):
        call1 = deterministic.match("generate daily report")
        assert call1 is not None
        assert call1.tool == "generate_daily_report"

        call2 = deterministic.match("daily report")
        assert call2 is not None
        assert call2.tool == "generate_daily_report"

        call3 = deterministic.match("write a daily report")
        assert call3 is not None
        assert call3.tool == "generate_daily_report"

        call4 = deterministic.match("work time today")
        assert call4 is not None
        assert call4.tool == "get_work_time_today"
