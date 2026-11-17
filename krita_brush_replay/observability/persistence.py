
from __future__ import annotations

import logging

from krita_brush_replay.app.recording_store import (
    FailureListener,
    Recording,
    RecordingStore,
    SavedRecording,
)
from krita_brush_replay.domain.recording import ContextRef
from krita_brush_replay.infrastructure.storage.background_writer import Work, WorkQueue
from krita_brush_replay.observability.log import log

logger = logging.getLogger(__name__)


class ObservedWorker:
    def __init__(self, worker: WorkQueue) -> None:
        self._worker = worker

    def submit(self, key: object, action: Work) -> None:
        def execute() -> None:
            try:
                action()
            except BaseException as error:
                log(logger, logging.ERROR, "persistence-work-failed", error=error)
                raise

        self._worker.submit(key, execute)

    def wait_until_done(self, key: object, timeout_s: float | None) -> bool:
        return self._worker.wait_until_done(key, timeout_s)


class ObservedArchive:
    def __init__(self, archive: RecordingStore) -> None:
        self._archive = archive

    def watch_failures(self, listener: FailureListener) -> None:
        self._archive.watch_failures(listener)

    def remember(self, saved: SavedRecording) -> None:
        self._archive.remember(saved)

    def retire(self, recording: Recording) -> None:
        self._archive.retire(recording)

    def close(
        self, timeout_s: float | None = None, *, keep: Recording | None = None
    ) -> bool:
        return self._archive.close(timeout_s, keep=keep)

    def create(self, document: str | None, origin: ContextRef) -> Recording:
        recording = self._archive.create(document, origin)
        log(logger, logging.DEBUG, "recording-created document=%s", document)
        return recording

    def recall(self, document: str) -> SavedRecording | None:
        return self._archive.recall(document)
