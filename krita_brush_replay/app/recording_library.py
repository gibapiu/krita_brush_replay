
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from krita_brush_replay.app.recording_store import FailureListener, Recording, SavedRecording


@dataclass(frozen=True, slots=True)
class RecordingKey:
    document: str | None
    recording_id: str


@dataclass(frozen=True, slots=True)
class RecordingInfo:
    key: RecordingKey
    created: datetime | None


@dataclass(frozen=True, slots=True)
class LibraryState:
    recordings: tuple[RecordingInfo, ...] = ()
    busy: bool = False
    message: str = ""


@dataclass(frozen=True, slots=True)
class DeleteResult:
    removed: tuple[RecordingKey, ...]
    errors: tuple[str, ...]


Failed = Callable[[BaseException], None]


class LibraryStore(Protocol):
    def watch_failures(self, listener: FailureListener) -> None: ...

    def browse(self, done: Callable[[tuple[RecordingInfo, ...]], None], failed: Failed) -> None: ...

    def load(self, key: RecordingKey, done: Callable[[SavedRecording], None], failed: Failed) -> None: ...

    def remove(
        self, keys: tuple[RecordingKey, ...], wait_for: tuple[Recording, ...],
        done: Callable[[DeleteResult], None], failed: Failed,
    ) -> None: ...

    def release(self, recording: Recording) -> None: ...

    def close(self, timeout_s: float | None, *, keep: Recording | None = None) -> bool: ...


class RecordingBrowser:
    def __init__(self, store: LibraryStore, on_change: Callable[[LibraryState], None]) -> None:
        self._store = store
        self._on_change = on_change
        self._state = LibraryState()
        self._revision = 0
        self._closed = False
        self._refresh_requested = False
        store.watch_failures(self._saving_failed)

    @property
    def state(self) -> LibraryState:
        return self._state

    def refresh(self) -> None:
        if self._closed:
            return
        if self._state.busy:
            self._refresh_requested = True
            return
        self._refresh_requested = False
        revision = self._begin("Loading…")

        def loaded(recordings):
            if self._valid(revision):
                self._update(LibraryState(recordings))
                if self._refresh_requested:
                    self.refresh()

        self._store.browse(loaded, lambda error: self._failed(revision, error))

    def load(self, key: RecordingKey, done: Callable[[SavedRecording], None], failed: Failed) -> None:
        revision = self._begin("Loading…")

        def loaded(saved):
            if not self._valid(revision):
                self._store.release(saved.recording)
                return
            self._update(LibraryState(self._state.recordings))
            done(saved)
            if self._refresh_requested:
                self.refresh()

        def error_received(error):
            if self._valid(revision):
                self._failed(revision, error)
                failed(error)

        self._store.load(key, loaded, error_received)

    def remove(
        self, keys: tuple[RecordingKey, ...], wait_for: tuple[Recording, ...],
        done: Callable[[], None],
    ) -> None:
        revision = self._begin("Deleting…")

        def removed(result):
            if not self._valid(revision):
                return
            entries = tuple(row for row in self._state.recordings if row.key not in result.removed)
            message = "" if not result.errors else result.errors[0]
            self._update(LibraryState(entries, message=message))
            done()

        def failed(error):
            if self._valid(revision):
                self._failed(revision, error)
                done()

        self._store.remove(keys, wait_for, removed, failed)

    def release(self, recording: Recording) -> None:
        self._store.release(recording)

    def close(self, timeout_s: float | None, *, keep: Recording | None = None) -> bool:
        self._closed = True
        self._revision += 1
        return self._store.close(timeout_s, keep=keep)

    def _begin(self, message: str) -> int:
        self._revision += 1
        self._update(LibraryState(self._state.recordings, True, message))
        return self._revision

    def _valid(self, revision: int) -> bool:
        return not self._closed and revision == self._revision

    def _failed(self, revision: int, error: BaseException) -> None:
        if self._valid(revision):
            self._update(LibraryState(self._state.recordings, message=str(error)))

    def _update(self, state: LibraryState) -> None:
        self._state = state
        self._on_change(state)

    def _saving_failed(self, recording: Recording, error: BaseException) -> None:
        if not self._closed:
            self._update(LibraryState(self._state.recordings, self._state.busy, str(error)))
