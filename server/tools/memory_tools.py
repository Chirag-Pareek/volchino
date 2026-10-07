"""Tools that answer from SQLite (spec §4: get_work_time_today)."""

from __future__ import annotations

from datetime import UTC, datetime, time

from server.tools.base import NoArgs, Tool, ToolContext


def _fmt(seconds: int, force_hours: bool = False) -> str:
    h, rem = divmod(max(0, seconds), 3600)
    m = rem // 60
    if h > 0 or force_hours:
        return f"{h}h {m}m"
    return f"{m}m"


async def get_work_time_today(_: NoArgs, ctx: ToolContext) -> str:
    local_midnight = datetime.combine(datetime.now().date(), time.min).astimezone()
    since_local = local_midnight.isoformat()
    since_utc = local_midnight.astimezone(UTC).isoformat()
    async with ctx.db.execute(
        """SELECT COALESCE(SUM(MAX(duration_seconds - idle_seconds, 0)), 0) AS active,
                  COUNT(*) AS n
           FROM activity WHERE timestamp >= ? OR timestamp >= ?""",
        (since_local, since_utc),
    ) as cur:
        row = await cur.fetchone()
    active = int(row["active"]) if row else 0
    if not row or row["n"] == 0:
        return "Work time today: 0h 0m (no activity tracked yet)."
    async with ctx.db.execute(
        """SELECT app_name, SUM(MAX(duration_seconds - idle_seconds, 0)) AS s FROM activity
           WHERE timestamp >= ? OR timestamp >= ? GROUP BY app_name ORDER BY s DESC LIMIT 3""",
        (since_local, since_utc),
    ) as cur:
        top = [f"{r['app_name']} {_fmt(int(r['s']))}" for r in await cur.fetchall()]
    suffix = f" (top: {', '.join(top)})." if top else "."
    return f"Work time today: {_fmt(active, force_hours=True)}{suffix}"


async def generate_daily_report(_: NoArgs, ctx: ToolContext) -> str:
    from server.obsidian.report_writer import write_daily_report

    path, summary = await write_daily_report(ctx.db, ctx.settings)
    return f"{summary} Saved to {path.name}."


TOOLS = [
    Tool(
        "get_work_time_today",
        "system:get_work_time",
        "Active work time today",
        NoArgs,
        get_work_time_today,
        cache_ttl_s=60,
    ),
    Tool(
        "generate_daily_report",
        "obsidian:append_daily_log",
        "Generate Obsidian daily activity report",
        NoArgs,
        generate_daily_report,
    ),
]
