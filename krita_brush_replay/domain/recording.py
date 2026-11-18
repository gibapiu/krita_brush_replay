"""Immutable data captured for brush-stroke replay."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from math import hypot

from krita_brush_replay.domain.samples import BrushSample as BrushSample
from krita_brush_replay.domain.samples import PackedSamples as PackedSamples

DEFAULT_MAX_SPEED_PIXELS_PER_MS = 30.0


class InputPhase(Enum):
    """The part of a freehand input gesture represented by a sample."""

    PRESS = auto()
    MOVE = auto()
    RELEASE = auto()


@dataclass(frozen=True, slots=True)
class ContextRef:
    """Identity of the document and paint layer bound to a recording."""

    document_id: str
    layer_id: str

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("document_id must not be empty")
        if not self.layer_id:
            raise ValueError("layer_id must not be empty")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """Stable identity for a Krita resource used by a brush."""

    resource_type: str
    name: str
    filename: str

    def __post_init__(self) -> None:
        if not self.resource_type:
            raise ValueError("resource_type must not be empty")
        if not self.name:
            raise ValueError("resource name must not be empty")


@dataclass(frozen=True, slots=True)
class ColorState:
    """A managed colour represented without retaining a mutable Krita object."""

    model: str
    depth: str
    profile: str
    components: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.model or not self.depth or not self.components:
            raise ValueError("colour state must include a colour space and components")


@dataclass(frozen=True, slots=True)
class BrushState:
    """Brush settings that Krita can reliably capture and restore."""

    preset: ResourceRef
    foreground: ColorState
    background: ColorState
    size: float
    opacity: float
    flow: float
    rotation: float
    pattern: ResourceRef
    gradient: ResourceRef
    blending_mode: str

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("brush size must be positive")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("brush opacity must be between 0.0 and 1.0")
        if not 0.0 <= self.flow <= 1.0:
            raise ValueError("brush flow must be between 0.0 and 1.0")
        if not self.blending_mode:
            raise ValueError("blending_mode must not be empty")


@dataclass(frozen=True, slots=True)
class Stroke:
    """A complete freehand input gesture."""

    samples: Sequence[BrushSample]
    brush_state: BrushState

    def __post_init__(self) -> None:
        if not isinstance(self.samples, PackedSamples):
            object.__setattr__(self, "samples", PackedSamples(self.samples))
        if not self.samples:
            raise ValueError("a stroke must contain at least one sample")


@dataclass(frozen=True, slots=True)
class RecordingSession:
    """All strokes and the document and layer where they were recorded."""

    origin: ContextRef
    strokes: tuple[Stroke, ...]

    @property
    def sample_count(self) -> int:
        return sum(len(stroke.samples) for stroke in self.strokes)


class HistoryStep(Enum):
    """A Krita undo-stack step taken while recording."""

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
            else:
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


def retained_strokes(history: Iterable[Stroke | HistoryStep]) -> tuple[Stroke, ...]:
    retained = RecordingHistory()
    for entry in history:
        retained.apply(entry)
    return retained.strokes


def normalized_input_speed(
    previous_position: tuple[float, float] | None,
    previous_timestamp_ms: int | None,
    position: tuple[float, float],
    timestamp_ms: int,
    *,
    max_speed_pixels_per_ms: float = DEFAULT_MAX_SPEED_PIXELS_PER_MS,
) -> float:
    """Estimate Krita's normalized speed sensor from consecutive view samples."""

    if previous_position is None or previous_timestamp_ms is None:
        return 0.0
    elapsed_ms = timestamp_ms - previous_timestamp_ms
    if elapsed_ms <= 0 or max_speed_pixels_per_ms <= 0:
        return 0.0
    distance = hypot(
        position[0] - previous_position[0],
        position[1] - previous_position[1],
    )
    return min(1.0, distance / elapsed_ms / max_speed_pixels_per_ms)
