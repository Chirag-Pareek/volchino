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
    host = "127.0.0.1"  # hard-bind; never 0.0.0.0 (spec §2)
    if settings.server_host != host:
        logging.getLogger(__name__).warning(
            "SERVER_HOST=%s overridden to %s (spec requirement)", settings.server_host, host
        )
    uvicorn.run(
        "server.main:create_app",
        host=host,
        port=settings.server_port,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
