
import logging

from krita_brush_replay.observability.log import log

logger = logging.getLogger(__name__)


def report_issue(operation: str, error: BaseException) -> None:
    log(logger, logging.WARNING, "%s", operation, error=error)


def callback_failed(error: Exception) -> None:
    log(logger, logging.ERROR, "ui-callback-failed", error=error)
