"""Obsidian Daily Report Generator.

Aggregates daily activity logs from SQLite, computes KPIs, app usage,
and deep work hours, writes clean Markdown to $VAULT_PATH/Daily/YYYY-MM-DD.md,
and updates Projects/<project>.md.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from server.config import Settings

log = logging.getLogger(__name__)

DEEP_WORK_APPS = frozenset(
    {
        "Code",
        "Kitty",
        "Terminal",
        "Obsidian",
        "Neovim",
        "Alacritty",
        "WezTerm",
        "Foot",
        "Android Studio",
        "Antigravity",
    }
)


def format_duration(seconds: int) -> str:
    """Format duration in seconds into human-friendly string (e.g., '2h 15m' or '45s')."""
    total = max(0, seconds)
    h, rem = divmod(total, 3600)
    m = rem // 60
    s = rem % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


def progress_bar(percentage: float, width: int = 16) -> str:
    """Render a clean text progress bar."""
    pct = max(0.0, min(100.0, percentage))
    filled = round((pct / 100.0) * width)
    return "█" * filled + "░" * (width - filled)


@dataclass
class DailyMetrics:
    target_date: date
    total_active_seconds: int = 0
    total_idle_seconds: int = 0
    total_duration_seconds: int = 0
    deep_work_seconds: int = 0
    app_durations: dict[str, int] = field(default_factory=dict)
    project_durations: dict[str, int] = field(default_factory=dict)
    workspace_durations: dict[str, int] = field(default_factory=dict)
    entry_count: int = 0


async def aggregate_activity_for_day(
    db: aiosqlite.Connection,
    target_date: date,
) -> DailyMetrics:
    """Aggregate activity logs from SQLite for the given calendar date."""
    start_dt = datetime.combine(target_date, time.min).astimezone()
    end_dt = datetime.combine(target_date, time.max).astimezone()

    start_local = start_dt.isoformat()
    end_local = end_dt.isoformat()
    start_utc = start_dt.astimezone(UTC).isoformat()
    end_utc = end_dt.astimezone(UTC).isoformat()

    metrics = DailyMetrics(target_date=target_date)

    query = """
        SELECT app_name, window_title, workspace, duration_seconds, idle_seconds,
               project_tag, timestamp
        FROM activity
        WHERE (timestamp >= ? AND timestamp <= ?)
           OR (timestamp >= ? AND timestamp <= ?)
        ORDER BY timestamp ASC
    """

    async with db.execute(query, (start_local, end_local, start_utc, end_utc)) as cur:
        rows = await cur.fetchall()

    metrics.entry_count = len(rows)
    for row in rows:
        app = row["app_name"] or "Unknown"
        duration = int(row["duration_seconds"] or 0)
        idle = int(row["idle_seconds"] or 0)
        active = max(0, duration - idle)
        project = row["project_tag"]
        workspace = str(row["workspace"] or "1")

        metrics.total_duration_seconds += duration
        metrics.total_idle_seconds += idle
        metrics.total_active_seconds += active

        metrics.app_durations[app] = metrics.app_durations.get(app, 0) + active

        if app in DEEP_WORK_APPS:
            metrics.deep_work_seconds += active

        if project:
            metrics.project_durations[project] = (
                metrics.project_durations.get(project, 0) + active
            )

        metrics.workspace_durations[workspace] = (
            metrics.workspace_durations.get(workspace, 0) + active
        )

    # Sort app durations descending
    metrics.app_durations = dict(
        sorted(metrics.app_durations.items(), key=lambda item: item[1], reverse=True)
    )
    # Sort project durations descending
    metrics.project_durations = dict(
        sorted(metrics.project_durations.items(), key=lambda item: item[1], reverse=True)
    )
    # Sort workspace durations
    metrics.workspace_durations = dict(
        sorted(metrics.workspace_durations.items(), key=lambda item: item[1], reverse=True)
    )

    return metrics


def build_markdown_report(metrics: DailyMetrics) -> str:
    """Generate clean Obsidian-flavored Markdown for the daily report."""
    d_str = metrics.target_date.isoformat()
    active_fmt = format_duration(metrics.total_active_seconds)
    deep_fmt = format_duration(metrics.deep_work_seconds)
    idle_fmt = format_duration(metrics.total_idle_seconds)

    deep_ratio = (
        (metrics.deep_work_seconds / metrics.total_active_seconds * 100.0)
        if metrics.total_active_seconds > 0
        else 0.0
    )

    lines: list[str] = [
        "---",
        f"date: {d_str}",
        "type: daily-report",
        f'total_active_time: "{active_fmt}"',
        f'deep_work_hours: "{deep_fmt}"',
        f'idle_time: "{idle_fmt}"',
        "tags:",
        "  - volchino/daily-report",
        "  - telemetry",
        "---",
        "",
        f"# 📊 Daily Activity Report — {d_str}",
        "",
        f"> **Summary**: **{active_fmt}** active | **{deep_fmt}** deep work | **{idle_fmt}** idle",
        "",
        "## ⏱️ Focus & Deep Work",
        "| Metric | Duration | Notes |",
        "| :--- | :--- | :--- |",
        f"| **Total Active Time** | {active_fmt} | Total interactive work across all apps |",
        f"| **Deep Work** | {deep_fmt} ({deep_ratio:.1f}%) | Coding, terminal & knowledge work |",
        f"| **Idle Time** | {idle_fmt} | Inactive / screensaver / away |",
        "",
        "## 📱 App Breakdown",
        "| Application | Time Spent | % of Active | Progress |",
        "| :--- | :--- | :--- | :--- |",
    ]

    # Ensure Code, Kitty, Firefox, Obsidian are represented if active
    tot = metrics.total_active_seconds
    if not metrics.app_durations:
        lines.append("| *No active usage recorded* | 0m | 0% | `░░░░░░░░░░░░░░░░` |")
    else:
        for app, dur in metrics.app_durations.items():
            pct = (dur / tot * 100.0) if tot else 0.0
            bar = progress_bar(pct, width=14)
            lines.append(f"| **{app}** | {format_duration(dur)} | {pct:.1f}% | `{bar}` |")

    lines.extend([
        "",
        "## 🚀 Projects",
        "| Project | Time Spent | % of Active |",
        "| :--- | :--- | :--- |",
    ])

    if not metrics.project_durations:
        lines.append("| *General / untagged* | - | - |")
    else:
        for proj, dur in metrics.project_durations.items():
            pct = (dur / tot * 100.0) if tot else 0.0
            lines.append(
                f"| **[[Projects/{proj}|{proj}]]** | {format_duration(dur)} | {pct:.1f}% |"
            )

    if metrics.workspace_durations:
        lines.extend([
            "",
            "## 🖥️ Workspaces",
        ])
        for ws, dur in metrics.workspace_durations.items():
            lines.append(f"- **Workspace {ws}**: {format_duration(dur)}")

    lines.extend([
        "",
        "---",
        f"*Generated automatically by Volchino Personal AI Agent at {datetime.now():%H:%M:%S}.*",
        "",
    ])

    return "\n".join(lines)


def update_project_note(
    vault_path: Path,
    project: str,
    target_date: date,
    active_seconds: int,
) -> Path:
    """Create or update Projects/<project>.md with today's logged active time."""
    projects_dir = vault_path / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    proj_file = projects_dir / f"{project}.md"
    d_str = target_date.isoformat()
    duration_str = format_duration(active_seconds)
    entry_line = f"- **{d_str}**: {duration_str} active work ([[Daily/{d_str}|Daily Report]])"

    if not proj_file.is_file():
        content = (
            "---\n"
            f"project: {project}\n"
            "type: project\n"
            "status: ACTIVE\n"
            "tags:\n"
            "  - volchino/project\n"
            "---\n\n"
            f"# 🚀 Project: {project}\n\n"
            "## 📝 Overview\n"
            f"Active project workspace tracked by Volchino Personal AI Agent.\n\n"
            "## ⏱️ Activity Log\n"
            f"{entry_line}\n"
        )
        proj_file.write_text(content, encoding="utf-8")
        log.info("Created project note at %s", proj_file)
        return proj_file

    # File exists: update or append to Activity Log section
    text = proj_file.read_text(encoding="utf-8")
    log_header = "## ⏱️ Activity Log"
    alt_header = "## Activity Log"

    header_to_use = log_header if log_header in text else (
        alt_header if alt_header in text else None
    )

    if header_to_use:
        # Check if this date already has an entry
        lines = text.splitlines()
        updated = False
        new_lines: list[str] = []
        for line in lines:
            if line.strip().startswith(f"- **{d_str}**:") or f"[[Daily/{d_str}" in line:
                new_lines.append(entry_line)
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            # Append under the header
            insert_idx = -1
            for idx, line in enumerate(new_lines):
                if line.strip() == header_to_use:
                    insert_idx = idx + 1
                    break
            if insert_idx != -1:
                new_lines.insert(insert_idx, entry_line)
            else:
                new_lines.append(entry_line)
        text = "\n".join(new_lines) + "\n"
    else:
        text = text.rstrip() + f"\n\n## ⏱️ Activity Log\n{entry_line}\n"

    proj_file.write_text(text, encoding="utf-8")
    log.info("Updated project note at %s", proj_file)
    return proj_file


async def write_daily_report(
    db: aiosqlite.Connection,
    settings: Settings,
    target_date: date | None = None,
) -> tuple[Path, str]:
    """Generate and write the daily report to $VAULT_PATH/Daily/YYYY-MM-DD.md."""
    t_date = target_date or datetime.now().date()
    metrics = await aggregate_activity_for_day(db, t_date)

    vault = settings.vault_path or (Path.home() / "Documents" / "Obsidian Vault")
    daily_dir = vault / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    report_path = daily_dir / f"{t_date.isoformat()}.md"
    markdown_content = build_markdown_report(metrics)
    report_path.write_text(markdown_content, encoding="utf-8")

    # Update active project files
    for project, sec in metrics.project_durations.items():
        if sec > 0:
            update_project_note(vault, project, t_date, sec)

    summary = (
        f"Daily report for {t_date}: {format_duration(metrics.total_active_seconds)} active time "
        f"({format_duration(metrics.deep_work_seconds)} deep work, "
        f"{format_duration(metrics.total_idle_seconds)} idle) "
        f"across {len(metrics.app_durations)} apps."
    )
    log.info("Wrote daily report to %s", report_path)
    return report_path, summary


async def schedule_daily_report_loop(
    db: aiosqlite.Connection,
    settings: Settings,
) -> None:
    """Background task running at 23:59 daily to generate the report."""
    log.info("Scheduled daily report generator task active (targets 23:59 daily)")
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            # Already past 23:59 today, schedule for tomorrow
            target += timedelta(days=1)

        wait_seconds = max(1.0, (target - now).total_seconds())
        log.debug("Waiting %.1f seconds until 23:59 daily report trigger", wait_seconds)
        try:
            await asyncio.sleep(wait_seconds)
            await write_daily_report(db, settings, target_date=datetime.now().date())
            # Sleep past 23:59 so we don't trigger multiple times in the same minute
            await asyncio.sleep(70.0)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Error during scheduled daily report generation")
            await asyncio.sleep(60.0)
