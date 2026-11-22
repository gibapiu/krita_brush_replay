"""Read captured strokes independently of the recording writer."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from krita_brush_replay.domain.brush_samples import decode_samples
from krita_brush_replay.domain.recording import BrushSample, BrushState, Stroke
from krita_brush_replay.infrastructure.recording_format import (
    BRUSH,
    FINISH,
    HEADER,
    SAMPLES,
    STROKE,
    decode_brush,
    read_record,
)


class _Samples(Sequence[BrushSample]):
    def __init__(self, stream, offset: int, count: int) -> None:
        self._stream = stream
        self._offset = self._next = offset
        self._count = count
        self._start = 0
        self._chunk: tuple[BrushSample, ...] = ()

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(self._count)[index])
        index = range(self._count)[index]
        if index < self._start:
            self._next, self._start, self._chunk = self._offset, 0, ()
        while index >= self._start + len(self._chunk):
            self._start += len(self._chunk)
            kind, data, self._next = read_record(self._stream, self._next)
            if kind != SAMPLES:
                raise OSError("recording samples are incomplete")
            self._chunk = tuple(decode_samples(data))
            if not self._chunk or self._start + len(self._chunk) > self._count:
                raise OSError("invalid recording sample count")
        return self._chunk[index - self._start]


class RecordingReader:
    def __init__(self, path: str) -> None:
        self._stream = open(path, "rb")
        self._brushes: OrderedDict[int, BrushState] = OrderedDict()

    def _metadata(self, reference: int) -> tuple[int, int, int]:
        kind, data, _ = read_record(self._stream, reference - HEADER.size - STROKE.size)
        if kind != FINISH or len(data) != STROKE.size:
            raise OSError("recording stroke is incomplete")
        return STROKE.unpack(data)

    def brush(self, reference: int) -> BrushState:
        if reference not in self._brushes:
            kind, data, _ = read_record(self._stream, reference)
            if kind != BRUSH:
                raise OSError("recording brush is missing")
            self._brushes[reference] = decode_brush(data)
            if len(self._brushes) > 64:
                self._brushes.popitem(last=False)
        self._brushes.move_to_end(reference)
        return self._brushes[reference]

    def stroke(self, reference: int) -> Stroke:
        brush, samples, count = self._metadata(reference)
        return Stroke(_Samples(self._stream, samples, count), self.brush(brush))

    def close(self) -> None:
        self._stream.close()
        self._brushes.clear()
