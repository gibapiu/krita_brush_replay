import logging

import pytest

from krita_brush_replay.observability.log import log


def test_wrapper_uses_host_logging_levels_and_handlers(caplog) -> None:
    logger = logging.getLogger("krita_brush_replay.logging-test")
    handlers = logger.handlers[:]
    propagate = logger.propagate
    caplog.set_level(logging.WARNING, logger=logger.name)

    log(logger, logging.DEBUG, "quiet detail=%s", 0.25)
    log(logger, logging.ERROR, "failure=%s", "paint")

    assert caplog.messages == ["failure=paint"]
    assert logger.handlers == handlers
    assert logger.propagate == propagate
    assert logger.level == logging.WARNING


@pytest.mark.parametrize("level", [logging.DEBUG, logging.ERROR])
def test_broken_handler_cannot_escape_logging_wrapper(level) -> None:
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise OSError("output unavailable")

    logger = logging.Logger("broken-handler", level=logging.DEBUG)
    logger.addHandler(BrokenHandler())

    log(logger, level, "activity=%s", "test")
