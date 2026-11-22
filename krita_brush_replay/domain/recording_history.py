from collections.abc import Callable
from enum import Enum, auto


class HistoryStep(Enum):
    UNDO = auto()
    REDO = auto()


class RecordingHistory:
    """Track retained strokes without owning their data."""

    def __init__(self) -> None:
        self.stroke_count = 0
        self.sample_count = 0
        self._redo_count = 0
        self._before_recording = 0

    def append(self, sample_count: int) -> None:
        self.stroke_count += 1
        self.sample_count += sample_count
        self.discard_redo()

    def discard_redo(self) -> None:
        self._redo_count = self.stroke_count
        self._before_recording = 0

    def apply(self, step: HistoryStep, samples_at: Callable[[int], int]) -> None:
        if step is HistoryStep.UNDO:
            if self.stroke_count:
                count = samples_at(self.stroke_count - 1)
                self.stroke_count -= 1
                self.sample_count -= count
            else:
                self._before_recording += 1
        elif self._before_recording:
            self._before_recording -= 1
        elif self.stroke_count < self._redo_count:
            self.sample_count += samples_at(self.stroke_count)
            self.stroke_count += 1
