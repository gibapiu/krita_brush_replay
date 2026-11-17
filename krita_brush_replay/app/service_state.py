
from dataclasses import dataclass
from enum import Enum, auto

from krita_brush_replay.domain.brush_override import StrokeSettings
from krita_brush_replay.app.recording_library import RecordingKey

READY = "Ready. Record strokes on one paint layer."


class ServiceMode(Enum):
    IDLE = auto()
    RECORDING = auto()
    REPLAYING = auto()
    LOADING = auto()
    CLOSING = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class ServiceState:
    mode: ServiceMode
    strokes: tuple[StrokeSettings, ...]
    sample_count: int
    message: str
    source: RecordingKey | None = None
    current_recording: RecordingKey | None = None
    document: str | None = None
    recorded_strokes: int = 0
    recorded_samples: int = 0
    document_open: bool = False

    @property
    def stroke_count(self) -> int:
        return len(self.strokes)

    @property
    def can_record(self) -> bool:
        return self.mode is ServiceMode.IDLE

    @property
    def can_stop(self) -> bool:
        return self.mode is ServiceMode.RECORDING

    @property
    def can_replay(self) -> bool:
        return self.mode is ServiceMode.IDLE and self.source is not None and self.stroke_count > 0
