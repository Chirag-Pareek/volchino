"""Activity tracker daemon for Hyprland window/workspace events and idle detection."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from server.config import Settings

from server.telemetry.hyprland_ipc import (
    get_hyprland_socket2_path,
    parse_activewindow_data,
    query_initial_state,
    stream_socket_events,
)
from server.telemetry.idle import IdleDetector
from server.telemetry.projects import detect_project, normalize_app_name

log = logging.getLogger(__name__)


@dataclass
class ActivityRecord:
    app_name: str
    window_title: str
    workspace: str
    duration_seconds: int
    idle_seconds: int
    project_tag: str | None
    timestamp: str

    def to_row(self) -> tuple[str, str, str, int, int, str | None, str]:
        return (
            self.app_name,
            self.window_title,
            self.workspace,
            self.duration_seconds,
            self.idle_seconds,
            self.project_tag,
            self.timestamp,
        )


class ActivityTracker:
    """Monitors Hyprland activity, tracks durations, detects idle time, and flushes to SQLite."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        settings: Settings,
        idle_detector: IdleDetector | None = None,
        batch_interval_s: float | None = None,
        tick_interval_s: float = 1.0,
    ) -> None:
        self.db = db
        self.settings = settings
        self.idle_detector = idle_detector or IdleDetector(
            idle_threshold_s=settings.activity_idle_threshold_s,
        )
        self.batch_interval_s = (
            batch_interval_s if batch_interval_s is not None else settings.activity_batch_interval_s
        )
        self.tick_interval_s = tick_interval_s

        # Tracking state
        self.current_class: str = ""
        self.current_title: str = ""
        self.current_workspace: str = "1"
        self.current_app_name: str = "Unknown"
        self.current_project: str | None = None

        self._active_duration_buffer: float = 0.0
        self._idle_duration_buffer: float = 0.0
        self._unflushed_records: list[ActivityRecord] = []

        self._running: bool = False
        self._last_flush_time: float = time.monotonic()
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []

    def _now_iso(self) -> str:
        """Return local ISO 8601 timestamp with timezone offset."""
        return datetime.now().astimezone().isoformat()

    async def init_state(self) -> None:
        """Discover initial window and workspace state."""
        klass, title, ws = await query_initial_state()
        self.current_class = klass
        self.current_title = title
        self.current_workspace = ws or "1"
        self.current_app_name = normalize_app_name(klass) if klass else "Desktop"
        self.current_project = detect_project(
            title, self.current_app_name, self.settings.projects_dir
        )
        self._last_flush_time = time.monotonic()

    async def on_window_changed(self, new_class: str, new_title: str) -> None:
        """Handle window change event."""
        async with self._lock:
            if (new_class, new_title) == (self.current_class, self.current_title):
                return

            self._record_current_buffer()

            self.current_class = new_class
            self.current_title = new_title
            self.current_app_name = normalize_app_name(new_class) if new_class else "Desktop"
            self.current_project = detect_project(
                new_title, self.current_app_name, self.settings.projects_dir
            )
            self.idle_detector.record_activity()

    async def on_workspace_changed(self, new_workspace: str) -> None:
        """Handle workspace switch event."""
        async with self._lock:
            new_ws = new_workspace.strip() or "1"
            if new_ws == self.current_workspace:
                return

            self._record_current_buffer()
            self.current_workspace = new_ws
            self.idle_detector.record_activity()

    def _record_current_buffer(self) -> None:
        """Cut the current duration chunk and buffer it for flushing."""
        total = self._active_duration_buffer + self._idle_duration_buffer
        if total >= 1.0:
            rec = ActivityRecord(
                app_name=self.current_app_name,
                window_title=self.current_title,
                workspace=self.current_workspace,
                duration_seconds=round(total),
                idle_seconds=round(self._idle_duration_buffer),
                project_tag=self.current_project,
                timestamp=self._now_iso(),
            )
            self._unflushed_records.append(rec)
        self._active_duration_buffer = 0.0
        self._idle_duration_buffer = 0.0

    async def tick(self, dt: float) -> None:
        """Periodic clock tick for duration accumulation and batch flushing."""
        is_idle = await self.idle_detector.is_idle()
        async with self._lock:
            if is_idle:
                self._idle_duration_buffer += dt
            else:
                self._active_duration_buffer += dt

            now = time.monotonic()
            if (now - self._last_flush_time) >= self.batch_interval_s:
                await self._flush_locked()

    async def flush(self) -> int:
        """Public method to flush buffered records to SQLite immediately."""
        async with self._lock:
            return await self._flush_locked()

    async def _flush_locked(self) -> int:
        """Insert buffered records into SQLite."""
        self._record_current_buffer()
        self._last_flush_time = time.monotonic()

        if not self._unflushed_records:
            return 0

        records_to_insert = [r.to_row() for r in self._unflushed_records]
        count = len(records_to_insert)
        try:
            await self.db.executemany(
                """INSERT INTO activity
                   (app_name, window_title, workspace,
                    duration_seconds, idle_seconds, project_tag, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                records_to_insert,
            )
            await self.db.commit()
            self._unflushed_records.clear()
            log.debug("Logged %d activity records to SQLite", count)
            return count
        except Exception:
            log.exception("Failed to flush activity records to SQLite")
            return 0

    async def _listen_socket(self) -> None:
        """Connect to Hyprland socket2 and dispatch events."""
        while self._running:
            sock_path = get_hyprland_socket2_path()
            if not sock_path or not sock_path.exists():
                log.debug("Hyprland socket2 not available; retrying in 5s...")
                await asyncio.sleep(5.0)
                continue

            try:
                reader, writer = await asyncio.open_unix_connection(str(sock_path))
                log.info("Connected to Hyprland socket2 at %s", sock_path)
                try:
                    async for event, data in stream_socket_events(reader):
                        if not self._running:
                            break
                        if event == "activewindow":
                            klass, title = parse_activewindow_data(data)
                            await self.on_window_changed(klass, title)
                        elif event == "workspace":
                            await self.on_workspace_changed(data)
                        elif event == "focusedmon":
                            # data format: mon,workspace
                            _, _, ws = data.partition(",")
                            if ws:
                                await self.on_workspace_changed(ws)
                finally:
                    writer.close()
                    await writer.wait_closed()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Hyprland socket connection lost: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3.0)

    async def _tick_loop(self) -> None:
        """Periodic tick loop."""
        while self._running:
            start = time.monotonic()
            try:
                await self.tick(self.tick_interval_s)
            except Exception:
                log.exception("Error during activity tracker tick")
            elapsed = time.monotonic() - start
            sleep_time = max(0.05, self.tick_interval_s - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    async def start(self) -> None:
        """Start the activity tracker background tasks."""
        if self._running:
            return
        self._running = True
        await self.init_state()
        self._tasks = [
            asyncio.create_task(self._listen_socket(), name="hyprland_socket_listener"),
            asyncio.create_task(self._tick_loop(), name="activity_tracker_tick_loop"),
        ]
        log.info("Activity tracker started (batch_interval=%ss)", self.batch_interval_s)

    async def stop(self) -> None:
        """Stop tracking and flush remaining data."""
        if not self._running:
            return
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.idle_detector.close()
        await self.flush()
        log.info("Activity tracker stopped")
