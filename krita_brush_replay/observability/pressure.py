from __future__ import annotations

from dataclasses import dataclass, field

from krita_brush_replay.domain.recording import BrushSample


@dataclass
class PressureTrace:
    count: int = 0
    minimum: float = 1.0
    maximum: float = 0.0
    first: float = 0.0
    last: float = 0.0
    started_ms: int = 0
    ended_ms: int = 0
    head: list[float] = field(default_factory=list)

    def observe(self, sample: BrushSample) -> None:
        self.observe_pressure(sample.pressure, sample.timestamp_ms)

    def observe_pressure(self, pressure: float, timestamp_ms: int) -> None:
        if self.count == 0:
            self.first = pressure
            self.started_ms = timestamp_ms
        self.count += 1
        self.minimum = min(self.minimum, pressure)
        self.maximum = max(self.maximum, pressure)
        self.last = pressure
        self.ended_ms = timestamp_ms
        if len(self.head) < 8:
            self.head.append(round(pressure, 5))

    def __str__(self) -> str:
        if not self.count:
            return "samples=0"
        return (
            f"samples={self.count} pressure_min={self.minimum:.5f} "
            f"pressure_max={self.maximum:.5f} pressure_first={self.first:.5f} "
            f"pressure_last={self.last:.5f} pressure_head={self.head} "
            f"duration_ms={max(0, self.ended_ms - self.started_ms)}"
        )
