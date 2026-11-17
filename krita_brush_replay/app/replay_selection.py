
from __future__ import annotations

from krita_brush_replay.app.recording_library import RecordingBrowser, RecordingKey
from krita_brush_replay.app.recording_store import Recording, SavedRecording
from krita_brush_replay.app.recording_workspace import RecordingWorkspace
from krita_brush_replay.domain.recording_model import RecordingModel


class ReplaySelection:
    def __init__(self, workspace: RecordingWorkspace, library: RecordingBrowser) -> None:
        self._workspace, self._library = workspace, library
        self._saved: SavedRecording | None = None
        self._model: RecordingModel | None = None
        self._key: RecordingKey | None = None

    @property
    def key(self) -> RecordingKey | None:
        return self._workspace.key if self.current else self._key

    @property
    def current(self) -> bool:
        return self._key is not None and self._saved is None

    @property
    def recording(self) -> Recording | None:
        return None if self._saved is None else self._saved.recording

    @property
    def model(self) -> RecordingModel:
        return self._workspace.model if self._model is None else self._model

    def select_current(self) -> None:
        self.clear()
        self._key = self._workspace.key

    def select(self, key: RecordingKey, saved: SavedRecording) -> None:
        self.clear()
        self._saved, self._key = saved, key
        self._model = RecordingModel(saved.session.origin, saved.session.strokes, saved.settings)

    def edit(self, indexes, change) -> None:
        if self._saved is None:
            self._workspace.edit(indexes, change)
        else:
            self.model.edit(indexes, change)
            self._saved.recording.keep_overrides(self.model.overrides)

    def clear(self) -> Recording | None:
        previous = self.recording
        if previous is not None:
            self._library.release(previous)
        self._saved, self._model, self._key = None, None, None
        return previous
