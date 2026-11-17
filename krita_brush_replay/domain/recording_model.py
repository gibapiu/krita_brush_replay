
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

from krita_brush_replay.domain.brush_override import BrushStatePatch, StrokeSettings
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    HistoryStep,
    RecordingSession,
    Stroke,
    retained_strokes,
)


class RecordingModel:

    def __init__(
        self,
        origin: ContextRef | None = None,
        strokes: tuple[Stroke, ...] = (),
        settings: tuple[StrokeSettings, ...] | None = None,
    ) -> None:
        if settings is None:
            settings = tuple(StrokeSettings(stroke.brush_state) for stroke in strokes)
        if len(strokes) != len(settings):
            raise ValueError("every stroke must have settings")
        self._origin = origin
        self._history: list[Stroke | HistoryStep] = list(strokes)
        self._strokes = list(strokes)
        self._settings = list(settings)
        self._sample_count = sum(len(stroke.samples) for stroke in strokes)

    @property
    def origin(self) -> ContextRef | None:
        return self._origin

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def settings(self) -> tuple[StrokeSettings, ...]:
        return tuple(self._settings)

    @property
    def overrides(self) -> tuple[BrushStatePatch, ...]:
        return tuple(settings.override for settings in self._settings)

    def session(self, *, edited: bool = False) -> RecordingSession | None:
        if self.origin is None:
            return None
        strokes = tuple(self._strokes)
        if edited:
            strokes = tuple(
                replace(stroke, brush_state=settings.effective)
                for stroke, settings in zip(strokes, self._settings)
            )
        return RecordingSession(self.origin, strokes)

    def append(self, entry: Stroke | HistoryStep) -> None:
        if isinstance(entry, Stroke):
            self._strokes.append(entry)
            self._settings.append(StrokeSettings(entry.brush_state))
            self._sample_count += len(entry.samples)
            self._history = self._strokes.copy()
            return
        self._history.append(entry)
        strokes = retained_strokes(self._history)
        unchanged = 0
        for previous, retained in zip(self._strokes, strokes):
            if previous is not retained:
                break
            unchanged += 1
        self._settings[unchanged:] = [
            StrokeSettings(stroke.brush_state) for stroke in strokes[unchanged:]
        ]
        self._strokes = list(strokes)
        self._sample_count = sum(len(stroke.samples) for stroke in strokes)

    def edit(
        self, indexes: Iterable[int], change: Callable[[StrokeSettings], StrokeSettings]
    ) -> None:
        edited = {index: change(self._existing(index)) for index in indexes}
        for index, settings in edited.items():
            self._settings[index] = settings

    def _existing(self, index: int) -> StrokeSettings:
        if index not in range(len(self._settings)):
            raise IndexError(f"stroke {index + 1} does not exist")
        return self._settings[index]


class StrokeCapture:

    def __init__(self) -> None:
        self._brush: BrushState | None = None
        self._samples: list[BrushSample] = []

    @property
    def active(self) -> bool:
        return self._brush is not None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def begin(self, sample: BrushSample, brush: BrushState) -> None:
        self._brush = brush
        self._samples = [sample]

    def move(self, sample: BrushSample) -> None:
        if self.active:
            self._samples.append(sample)

    def finish(self, current_size: float | None = None) -> Stroke | None:
        brush = self._brush
        if brush is None:
            return None
        try:
            if current_size is not None and current_size != brush.size:
                return None
            return Stroke(self._samples, brush)
        finally:
            self.discard()

    def discard(self) -> None:
        self._brush = None
        self._samples.clear()
