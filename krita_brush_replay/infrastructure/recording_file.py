"""Append captured data to one temporary file in bounded batches."""

from __future__ import annotations

from collections import OrderedDict
from tempfile import TemporaryDirectory

from krita_brush_replay.app.recording_store import StrokeReference
from krita_brush_replay.domain.brush_samples import CompactBrushSamplesBuilder
from krita_brush_replay.domain.recording import BrushSample, BrushState, Stroke
from krita_brush_replay.infrastructure.recording_format import (
    BRUSH,
    FINISH,
    HEADER,
    MAGIC,
    SAMPLES,
    encode_brush,
    encode_stroke,
)
from krita_brush_replay.infrastructure.recording_reader import RecordingReader
from krita_brush_replay.infrastructure.recording_writer import WRITE_BYTES, RecordingWriter

CHUNK_SAMPLES = 256


class RecordingFile:
    def __init__(self) -> None:
        self._directory: TemporaryDirectory | None = None
        self._writer: RecordingWriter | None = None
        self._reader: RecordingReader | None = None
        self._buffer = bytearray()
        self._offset = len(MAGIC)
        self._pending = CompactBrushSamplesBuilder()
        self._active_count = 0
        self._active_brush: int | None = None
        self._sample_start = 0
        self._brush_offsets: OrderedDict[BrushState, int] = OrderedDict()
        self._closed = False

    @property
    def active_sample_count(self) -> int:
        return self._active_count

    def _check_writer(self) -> None:
        if self._closed:
            raise OSError("recording file is closed")
        self._writer.check()

    def reset(self) -> None:
        if self._closed:
            raise OSError("recording file is closed")
        if self._directory is None:
            self._directory = TemporaryDirectory(
                prefix="krita-brush-replay-", ignore_cleanup_errors=True
            )
            self._writer = RecordingWriter(self._directory.name + "/recording.bin")
        self._writer.reset(MAGIC)
        try:
            if self._reader is not None:
                self._reader.close()
            self._reader = RecordingReader(self._directory.name + "/recording.bin")
            self._buffer.clear()
            self._offset = len(MAGIC)
            self._brush_offsets.clear()
            self.discard()
        except Exception:
            self._writer.close()
            raise

    def _record(self, kind: int, data: bytes = b"", *, flush: bool = True) -> int:
        self._check_writer()
        offset = self._offset
        self._buffer.extend(HEADER.pack(kind, len(data)))
        self._buffer.extend(data)
        self._offset += HEADER.size + len(data)
        if flush and len(self._buffer) >= WRITE_BYTES:
            self._send()
        return offset

    def _send(self) -> None:
        if self._buffer:
            self._writer.append(bytes(self._buffer))
            self._buffer.clear()

    def begin(self, brush: BrushState) -> None:
        self._check_writer()
        if brush not in self._brush_offsets:
            self._brush_offsets[brush] = self._record(BRUSH, encode_brush(brush))
            if len(self._brush_offsets) > 64:
                self._brush_offsets.popitem(last=False)
        self._active_brush = self._brush_offsets[brush]
        self._brush_offsets.move_to_end(brush)
        self._sample_start = self._offset

    def append(self, sample: BrushSample) -> None:
        self._check_writer()
        self._pending.append(sample)
        self._active_count += 1
        if len(self._pending) == CHUNK_SAMPLES:
            self._flush_samples()

    def _flush_samples(self, *, flush: bool = True) -> None:
        if len(self._pending):
            self._record(SAMPLES, bytes(self._pending.finish()), flush=flush)

    def finish(self, *, flush: bool = True) -> StrokeReference | None:
        if self._active_brush is None:
            return None
        self._flush_samples(flush=flush)
        count = self._active_count
        brush = self._active_brush
        self._record(FINISH, encode_stroke(brush, self._sample_start, count), flush=flush)
        self.discard()
        return StrokeReference(
            end_offset=self._offset, brush_reference=brush, sample_count=count
        )

    def discard(self) -> None:
        if len(self._pending):
            self._pending = CompactBrushSamplesBuilder()
        self._active_count = 0
        self._active_brush = None

    def stop(self) -> None:
        self.discard()
        self._writer.finish(bytes(self._buffer))
        self._buffer.clear()

    def abort(self) -> int:
        written = self._writer.abort() if self._writer is not None else 0
        self.discard()
        self._buffer.clear()
        return written

    def read_brush(self, reference: int) -> BrushState:
        return self._reader.brush(reference)

    def read_stroke(self, reference: int) -> Stroke:
        return self._reader.stroke(reference)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._writer is not None:
                self._writer.close()
        finally:
            if self._reader is not None:
                self._reader.close()
            self._buffer.clear()
            self._brush_offsets.clear()
            if self._directory is not None:
                self._directory.cleanup()
