"""External ReplayHost double for Qt-independent application contracts."""

from __future__ import annotations

from random import Random
from threading import Event
from time import monotonic

from krita_brush_replay.app.service import BrushReplayService
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    InputPhase,
    ResourceRef,
)

ORIGIN = ContextRef("document", "recorded-layer")
RECORDED = BrushState(
    ResourceRef("preset", "Ink", "ink.kpp"),
    ColorState("RGBA", "U8", "sRGB", (0.1, 0.2, 0.3, 1.0)),
    ColorState("RGBA", "U8", "sRGB", (0.8, 0.7, 0.6, 1.0)),
    12.345,
    0.8,
    0.6,
    32.0,
    ResourceRef("pattern", "Paper", "paper.pat"),
    ResourceRef("gradient", "Ramp", "ramp.svg"),
    "multiply",
)


class ReplayHost:
    def __init__(self) -> None:
        self.context = ORIGIN
        self.brush = RECORDED
        self.outputs = []

    def active_context(self):
        return self.context

    def capture_brush_state(self):
        return self.brush

    def replay(self, session, on_complete, on_error, *, use_original_settings):
        self.outputs.append(session)
        on_complete()


def draw(service: BrushReplayService, x: float) -> None:
    service.accept_input(InputPhase.PRESS, BrushSample(x, 4.0, 0.5, 10))
    service.accept_input(InputPhase.RELEASE, None)


def varied_samples(count: int, *, seed: int = 1):
    random = Random(seed)
    for index in range(count):
        yield BrushSample(
            random.random(),
            random.random(),
            random.random(),
            index * 8,
            random.random(),
            random.random(),
            random.random(),
            random.random(),
            random.random(),
        )


def draw_samples(service: BrushReplayService, samples) -> None:
    points = iter(samples)
    service.accept_input(InputPhase.PRESS, next(points))
    for point in points:
        service.accept_input(InputPhase.MOVE, point)
    service.accept_input(InputPhase.RELEASE, None)


def record_when_available(service: BrushReplayService) -> None:
    deadline = monotonic() + 2
    pause = Event()
    while True:
        service.start_recording()
        if service.state.can_stop:
            return
        assert monotonic() < deadline, service.state.message
        pause.wait(0.01)
