"""Tools that answer from SQLite (spec §4: get_work_time_today)."""

from __future__ import annotations

from datetime import datetime, time

from server.tools.base import NoArgs, Tool, ToolContext


def _fmt(seconds: int) -> str:
    h, rem = divmod(max(0, seconds), 3600)
    return f"{h}h {rem // 60}m"


async def get_work_time_today(_: NoArgs, ctx: ToolContext) -> str:
    local_midnight = datetime.combine(datetime.now().date(), time.min).astimezone()
    since = local_midnight.astimezone().isoformat()
    async with ctx.db.execute(
        """SELECT COALESCE(SUM(MAX(duration_seconds - idle_seconds, 0)), 0) AS active,
                  COUNT(*) AS n
           FROM activity WHERE timestamp >= ?""",
        (since,),
    ) as cur:
        row = await cur.fetchone()
    active = int(row["active"]) if row else 0
    if not row or row["n"] == 0:
        return "Work time today: 0h 0m (no activity tracked yet)."
    async with ctx.db.execute(
        """SELECT app_name, SUM(MAX(duration_seconds - idle_seconds, 0)) AS s FROM activity
           WHERE timestamp >= ? GROUP BY app_name ORDER BY s DESC LIMIT 3""",
        (since,),
    ) as cur:
        top = [f"{r['app_name']} {_fmt(int(r['s']))}" for r in await cur.fetchall()]
    return f"Work time today: {_fmt(active)}" + (f" (top: {', '.join(top)})." if top else ".")


TOOLS = [
    Tool(
        "get_work_time_today",
        "system:get_work_time",
        "Active work time today",
        NoArgs,
        get_work_time_today,
        cache_ttl_s=60,
    ),
]
