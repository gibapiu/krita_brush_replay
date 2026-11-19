"""External ReplayHost double for Qt-independent application contracts."""

from __future__ import annotations

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
