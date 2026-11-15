"""Best-effort logging: host handler failures never reach capture or replay callbacks."""

from __future__ import annotations

import logging


def log(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
    error: Exception | None = None,
) -> None:
    try:
        logger.log(level, message, *args, exc_info=error)
    except Exception:
        pass
