"""Best-effort logging without configuring the host's handlers or levels."""

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
        logger.log(
            level,
            message,
            *args,
            exc_info=(type(error), error, error.__traceback__) if error is not None else None,
        )
    except Exception:
        # A broken host handler must not interrupt capture or replay callbacks.
        pass
