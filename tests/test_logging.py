import logging

from krita_brush_replay.observability.log import log


def test_broken_handler_cannot_escape_logging_wrapper() -> None:
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise OSError("output unavailable")

    logger = logging.Logger("broken-handler", level=logging.DEBUG)
    logger.addHandler(BrokenHandler())

    log(logger, logging.ERROR, "activity=%s", "test")
