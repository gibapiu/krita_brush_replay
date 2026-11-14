"""Log replay boundaries while preserving core objects and callback semantics."""

from __future__ import annotations

import logging

from krita_brush_replay.domain.recording import RecordingSession
from krita_brush_replay.observability.log import log

logger = logging.getLogger(__name__)


class ObservedReplayer:
    def __init__(self, replayer) -> None:
        self._replayer = replayer

    def replay(
        self,
        document,
        node,
        session: RecordingSession,
        target,
        on_complete,
        on_error,
        *,
        apply_brush_state=None,
    ) -> None:
        log(logger, logging.DEBUG, "replay-request strokes=%s", len(session.strokes))

        def complete() -> None:
            log(logger, logging.DEBUG, "replay-finished status=complete")
            on_complete()

        def fail(error: Exception) -> None:
            log(logger, logging.ERROR, "replay-failed", error=error)
            on_error(error)

        self._replayer.replay(
            document,
            node,
            session,
            target,
            complete,
            fail,
            apply_brush_state=apply_brush_state,
        )
