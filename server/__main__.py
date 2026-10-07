"""``python -m server`` — start uvicorn bound to 127.0.0.1."""

from __future__ import annotations

import logging

import uvicorn

from server.config import Settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)-24s %(levelname)-7s %(message)s"
)


def main() -> None:
    settings = Settings.from_env()
    host = settings.server_host or "127.0.0.1"
    uvicorn.run(
        "server.main:create_app",
        host=host,
        port=settings.server_port,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
