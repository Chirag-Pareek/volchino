"""Standalone entrypoint for Hyprland activity tracker daemon."""

from __future__ import annotations

import asyncio
import logging
import signal

from server import db
from server.config import Settings
from server.telemetry.tracker import ActivityTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("volchino.telemetry")


async def main() -> None:
    settings = Settings.from_env()
    database = await db.connect(settings.sqlite_path)
    tracker = ActivityTracker(database, settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("Starting standalone Hyprland activity tracker daemon...")
    await tracker.start()

    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down activity tracker...")
        await tracker.stop()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
