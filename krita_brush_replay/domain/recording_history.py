from __future__ import annotations

from enum import Enum, auto

from krita_brush_replay.domain.recording import Stroke


class HistoryStep(Enum):
    UNDO = auto()
    REDO = auto()


class RecordingHistory:
    def __init__(self) -> None:
        self._retained: list[Stroke] = []
        self._undone: list[Stroke] = []
        self._undone_before_recording = 0
        self._sample_count = 0

    @property
    def strokes(self) -> tuple[Stroke, ...]:
        return tuple(self._retained)

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def clear(self) -> None:
        self._retained.clear()
        self.discard_redo()
        self._sample_count = 0

    def discard_redo(self) -> None:
        self._undone.clear()
        self._undone_before_recording = 0

    def apply(self, entry: Stroke | HistoryStep) -> Stroke | None:
        if entry is HistoryStep.UNDO:
            if self._retained:
                stroke = self._retained.pop()
                self._undone.append(stroke)
                self._sample_count -= len(stroke.samples)
                return stroke
            self._undone_before_recording += 1
        elif entry is HistoryStep.REDO:
            if self._undone_before_recording:
                self._undone_before_recording -= 1
            elif self._undone:
                stroke = self._undone.pop()
                self._retained.append(stroke)
                self._sample_count += len(stroke.samples)
                return stroke
        else:
            self._retained.append(entry)
            self.discard_redo()
            self._sample_count += len(entry.samples)
            return entry
        return None
