
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from krita_brush_replay.app.recording_policy import StorageState
from krita_brush_replay.app.recording_library import RecordingKey
from krita_brush_replay.domain.brush_override import BrushStatePatch, StrokeSettings
from krita_brush_replay.domain.recording import ContextRef, HistoryStep, RecordingSession, Stroke


class Recording(Protocol):

    @property
    def key(self) -> RecordingKey: ...

    @property
    def storage(self) -> StorageState: ...

    def keep(self, entry: Stroke | HistoryStep) -> Stroke | HistoryStep: ...

    @property
    def released(self) -> bool: ...

    def park(self) -> None: ...

    def claim(self) -> bool: ...

    def seal(self, overrides: tuple[BrushStatePatch, ...]) -> None: ...

    def keep_overrides(self, overrides: tuple[BrushStatePatch, ...]) -> None: ...

    def follow(self, document: str) -> None: ...

    def close(self) -> None: ...

    def wait_until_saved(self, timeout_s: float | None = None) -> bool: ...

    def wait_until_flushed(self, timeout_s: float | None = None) -> bool: ...


FailureListener = Callable[[Recording, BaseException], None]


@dataclass(frozen=True, slots=True)
class SavedRecording:
    session: RecordingSession
    settings: tuple[StrokeSettings, ...]
    recovered: bool
    recording: Recording


class RecordingStore(Protocol):
    def watch_failures(self, listener: FailureListener) -> None: ...

    def create(self, document: str | None, origin: ContextRef) -> Recording: ...

    def recall(self, document: str) -> SavedRecording | None: ...

    def remember(self, saved: SavedRecording) -> None: ...

    def retire(self, recording: Recording) -> None: ...

    def close(
        self, timeout_s: float | None = None, *, keep: Recording | None = None
    ) -> bool: ...
