"""Storage boundary for captured strokes. History and edits stay above it."""

from typing import NamedTuple, Protocol

from krita_brush_replay.domain.recording import BrushSample, BrushState, Stroke


class StorageBusyError(OSError):
    """The previous writer has not exited; its file cannot be replaced yet."""


class StrokeReference(NamedTuple):
    end_offset: int
    brush_reference: int
    sample_count: int


class RecordingStore(Protocol):
    @property
    def active_sample_count(self) -> int: ...

    def reset(self) -> None: ...

    def begin(self, brush: BrushState) -> None: ...

    def append(self, sample: BrushSample) -> None: ...

    def finish(self, *, flush: bool = True) -> StrokeReference | None: ...

    def discard(self) -> None: ...

    def stop(self) -> None: ...

    def abort(self) -> int: ...

    def read_brush(self, reference: int) -> BrushState: ...

    def read_stroke(self, reference: int) -> Stroke: ...

    def close(self) -> None: ...
