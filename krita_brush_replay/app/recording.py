"""Own the recording's live index, history, and current overrides."""

from __future__ import annotations

from array import array
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TypeVar

from krita_brush_replay.app.recording_store import RecordingStore, StorageBusyError
from krita_brush_replay.domain.brush_override import BrushStatePatch, RecordingSettings
from krita_brush_replay.domain.recording import BrushSample, BrushState, Stroke
from krita_brush_replay.domain.recording_history import HistoryStep, RecordingHistory

T = TypeVar("T")
_EMPTY_PATCH = BrushStatePatch()


class _LiveRows(Sequence[T]):
    def __init__(self, count: Callable[[], int], read: Callable[[int], T]) -> None:
        self._count = count
        self._read = read

    def __len__(self) -> int:
        return self._count()

    def __getitem__(self, index):
        positions = range(len(self))
        if isinstance(index, slice):
            return tuple(self._read(row) for row in positions[index])
        return self._read(positions[index])


class Recording:
    def __init__(self, storage: RecordingStore) -> None:
        self._storage = storage
        self._history = RecordingHistory()
        self._stroke_ends = array("Q")
        self._brush_references = array("Q")
        self._sample_totals = array("Q")
        self._override = _EMPTY_PATCH
        self._generation = 0
        self._closed = False
        self.revision = 0

    def _view(self, read: Callable[[int], T]) -> Sequence[T]:
        generation = self._generation

        def count() -> int:
            if self._closed or generation != self._generation:
                raise OSError("recording is closed or replaced")
            return self._history.stroke_count

        return _LiveRows(count, read)

    @property
    def stroke_count(self) -> int:
        return self._history.stroke_count

    @property
    def sample_count(self) -> int:
        return self._history.sample_count + self._storage.active_sample_count

    def reset(self) -> None:
        try:
            self._storage.reset()
        except StorageBusyError:
            raise
        except Exception:
            self._generation += 1
            raise
        self._generation += 1
        self._history = RecordingHistory()
        self._stroke_ends = array("Q")
        self._brush_references = array("Q")
        self._sample_totals = array("Q")
        self._override = _EMPTY_PATCH
        self.revision += 1

    def begin(self, brush: BrushState) -> None:
        self._storage.begin(brush)

    def append(self, sample: BrushSample) -> None:
        self._storage.append(sample)

    def discard(self) -> None:
        self._storage.discard()

    def finish(self, *, flush: bool = True) -> None:
        captured = self._storage.finish(flush=flush)
        if captured is None:
            return
        row = self._history.stroke_count
        total = self._history.sample_count + captured.sample_count
        if row == len(self._stroke_ends):
            self._stroke_ends.append(captured.end_offset)
            self._brush_references.append(captured.brush_reference)
            self._sample_totals.append(total)
        else:
            self._stroke_ends[row] = captured.end_offset
            self._brush_references[row] = captured.brush_reference
            self._sample_totals[row] = total
        self._history.append(captured.sample_count)
        self.revision += 1

    def history(self, step: HistoryStep) -> None:
        previous = self._history.stroke_count
        self._history.apply(step, self._samples_at)
        if self._history.stroke_count != previous:
            self.revision += 1

    def _samples_at(self, row: int) -> int:
        return self._sample_totals[row] - (self._sample_totals[row - 1] if row else 0)

    def stop(self) -> None:
        self.finish(flush=False)
        self._storage.stop()
        self._discard_redo()

    def abort(self) -> None:
        written = self._storage.abort()
        while (
            self._history.stroke_count
            and self._stroke_ends[self._history.stroke_count - 1] > written
        ):
            self._history.apply(HistoryStep.UNDO, self._samples_at)
        self._discard_redo()
        self.revision += 1

    def _discard_redo(self) -> None:
        self._history.discard_redo()
        count = self._history.stroke_count
        del self._stroke_ends[count:]
        del self._brush_references[count:]
        del self._sample_totals[count:]

    def settings(self) -> RecordingSettings:
        count = self._history.stroke_count
        references = dict.fromkeys(self._brush_references[:count])
        brushes = (self._storage.read_brush(reference) for reference in references)
        return RecordingSettings.summarize(count, brushes, self._override)

    def strokes(self, *, effective: bool = False) -> Sequence[Stroke]:
        patch = self._override if effective else _EMPTY_PATCH

        def read(row: int) -> Stroke:
            stroke = self._storage.read_stroke(self._stroke_ends[row])
            if patch.overridden_settings:
                return replace(stroke, brush_state=patch.apply_to(stroke.brush_state))
            return stroke

        return self._view(read)

    def edit(self, change: Callable[[RecordingSettings], BrushStatePatch]) -> None:
        self._override = change(self.settings())
        self.revision += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._storage.close()
        finally:
            self._stroke_ends = array("Q")
            self._brush_references = array("Q")
            self._sample_totals = array("Q")
            self._override = _EMPTY_PATCH
