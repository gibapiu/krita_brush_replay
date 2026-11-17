
from __future__ import annotations

import logging

from krita_brush_replay.domain.recording import BrushSample, InputPhase
from krita_brush_replay.infrastructure.host.input_filter import CanvasInputFilter
from krita_brush_replay.observability.errors import report_issue
from krita_brush_replay.observability.log import log

logger = logging.getLogger(__name__)


class ObservedInputFilter(CanvasInputFilter):
    def __init__(self, host, handler, error_handler, parent=None) -> None:
        def accept(phase: InputPhase, sample: BrushSample | None) -> None:
            if phase is not InputPhase.MOVE:
                log(logger, logging.DEBUG, "capture phase=%s", phase.name)
            handler(phase, sample)

        def fail(error: Exception) -> None:
            log(logger, logging.ERROR, "capture-failed", error=error)
            error_handler(error)

        super().__init__(host, accept, fail, parent, report_issue=report_issue)
