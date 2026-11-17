
from __future__ import annotations

from collections.abc import Callable, Iterable

from krita_brush_replay.app.recording_policy import recording_status, stop_reason, warning
from krita_brush_replay.app.recording_library import RecordingKey
from krita_brush_replay.app.recording_store import Recording, RecordingStore, SavedRecording
from krita_brush_replay.app.service_state import READY
from krita_brush_replay.domain.brush_override import StrokeSettings
from krita_brush_replay.domain.recording import ContextRef, HistoryStep, Stroke
from krita_brush_replay.domain.recording_model import RecordingModel


class RecordingWorkspace:
    def __init__(self, store: RecordingStore) -> None:
        self._store = store
        self._recording: Recording | None = None
        self._document_id: str | None = None
        self._document: str | None = None
        self._model = RecordingModel()

    @property
    def document_id(self) -> str | None:
        return self._document_id

    @property
    def key(self) -> RecordingKey | None:
        return None if self._recording is None else self._recording.key

    @property
    def document(self) -> str | None:
        return self._document

    @property
    def model(self) -> RecordingModel:
        return self._model

    @property
    def _current(self) -> Recording:
        if self._recording is None:
            raise ValueError("No recording selected.")
        return self._recording

    @property
    def status(self) -> str:
        return READY if self._recording is None else recording_status(self._recording.storage)

    def owns(self, recording: Recording) -> bool:
        return recording is self._recording

    def start(self, document: str | None, origin: ContextRef) -> None:
        recording = self._store.create(document, origin)
        self.retire()
        self._recording = recording
        self._document_id = origin.document_id
        self._document = document
        self._model = RecordingModel(origin)

    def select(self, document_id: str | None, document: str | None) -> str | None:
        if document_id == self.document_id:
            return None
        if self._recording is not None:
            session = self.model.session()
            if session is None:
                raise RuntimeError("A selected recording must have an origin.")
            self._store.remember(
                SavedRecording(session, self.model.settings, False, self._recording)
            )
            self._recording = None
        self._model = RecordingModel()
        self._document_id = document_id
        self._document = document
        saved = None if document is None else self._store.recall(document)
        if saved is None:
            return READY
        self._recording = saved.recording
        self._model = RecordingModel(saved.session.origin, saved.session.strokes, saved.settings)
        count = len(saved.session.strokes)
        if saved.recovered:
            return f"Recovered {count} stroke(s) from an unfinished recording."
        return f"Restored {count} stroke(s) recorded on this document."

    def append(self, entry: Stroke | HistoryStep) -> str | None:
        before = self.model.sample_count
        self.model.append(self._current.keep(entry))
        return warning(self._current.storage, before, self.model.sample_count)

    def edit(
        self, indexes: Iterable[int], change: Callable[[StrokeSettings], StrokeSettings]
    ) -> None:
        self.model.edit(indexes, change)
        self._current.keep_overrides(self.model.overrides)

    def seal(self) -> None:
        self._current.seal(self.model.overrides)

    def stop_reason(self, drawing: int) -> str | None:
        return stop_reason(self._current.storage, self.model.sample_count, drawing)

    def follow(self, document: str) -> bool:
        self._document = document
        if self._recording is None:
            return False
        before = self.status
        self._recording.follow(document)
        return self.status != before

    def retire(self) -> None:
        if self._recording is not None:
            self._store.retire(self._recording)
            self._recording = None

    def forget(self) -> Recording | None:
        previous = self._recording
        self.retire()
        self._model = RecordingModel()
        return previous

    def close(self, timeout_s: float | None, *, keep_replay: bool = False) -> bool:
        if not keep_replay:
            self.retire()
        return self._store.close(timeout_s, keep=self._recording)

    def watch_failures(self, listener: Callable[[Recording, BaseException], None]) -> None:
        self._store.watch_failures(listener)
