import logging
from collections.abc import Callable
from dataclasses import replace

from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    RecordingSession,
    ResourceRef,
    Stroke,
)
from krita_brush_replay.infrastructure.krita_replay import KritaReplayer

CONTEXT = ContextRef("document", "layer")
BRUSH_STATE = BrushState(
    preset=ResourceRef("preset", "Basic", "basic.kpp"),
    foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
    background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
    size=10.0,
    opacity=1.0,
    flow=1.0,
    rotation=0.0,
    pattern=ResourceRef("pattern", "Canvas", "canvas.pat"),
    gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
    blending_mode="normal",
)


class FakeNode:
    def __init__(self) -> None:
        self.lines: list[tuple[object, object, float, float]] = []

    def paintLine(self, start, end, start_pressure, end_pressure) -> None:
        self.lines.append((start, end, start_pressure, end_pressure))


class FakeDocument:
    def __init__(self) -> None:
        self.barrier_results = [True]
        self.barrier_checks = 0
        self.unlocks = 0

    def tryBarrierLock(self) -> bool:
        self.barrier_checks += 1
        return self.barrier_results.pop(0)

    def unlock(self) -> None:
        self.unlocks += 1


class ImmediateScheduler:
    def call_soon(self, callback: Callable[[], None]) -> None:
        callback()

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None:
        callback()


class QueuedScheduler:
    def __init__(self) -> None:
        self.callbacks: list[tuple[int, Callable[[], None]]] = []

    def call_soon(self, callback: Callable[[], None]) -> None:
        self.call_later(0, callback)

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.callbacks.append((delay_ms, callback))

    def run_next(self) -> int:
        delay_ms, callback = self.callbacks.pop(0)
        callback()
        return delay_ms


def point_factory(x: float, y: float) -> tuple[float, float]:
    return (x, y)


def session_for(*strokes: tuple[BrushSample, ...]) -> RecordingSession:
    return RecordingSession(
        CONTEXT,
        tuple(Stroke(samples, BRUSH_STATE) for samples in strokes),
    )


def replayer(**kwargs) -> KritaReplayer:
    return KritaReplayer(lambda: CONTEXT, **kwargs)


class FakeStrokePoint:
    def __init__(
        self,
        position,
        pressure,
        x_tilt,
        y_tilt,
        rotation,
        tangential_pressure,
        time,
        speed,
    ) -> None:
        self.position = position
        self.pressure = pressure
        self.x_tilt = x_tilt
        self.y_tilt = y_tilt
        self.rotation = rotation
        self.tangential_pressure = tangential_pressure
        self.time = time
        self.speed = speed


class ContinuousNode(FakeNode):
    def __init__(self) -> None:
        super().__init__()
        self.polyline_strokes: list[list[FakeStrokePoint]] = []

    def paintPolylineStroke(self, points: list[FakeStrokePoint]) -> None:
        self.polyline_strokes.append(points)


class FakeSyntheticInput:
    def __init__(self) -> None:
        self.target = object()
        self.strokes = []

    def prepare(self):
        return self.target

    def replay_stroke(
        self,
        target,
        samples,
        require_context,
        on_complete,
        on_error,
        *,
        before_press=None,
    ) -> None:
        require_context()
        if before_press is not None:
            before_press()
        self.strokes.append((target, samples))
        on_complete()


def test_continuous_api_receives_all_recorded_input_axes(caplog) -> None:
    caplog.set_level(logging.INFO, logger="krita_brush_replay.infrastructure.krita_replay")
    node = ContinuousNode()
    synthetic_input = FakeSyntheticInput()
    samples = (
        BrushSample(
            1.25,
            2.5,
            0.2,
            100,
            x_tilt=-10.0,
            y_tilt=12.0,
            rotation=45.0,
            tangential_pressure=0.3,
            speed=0.4,
        ),
        BrushSample(
            3.75,
            4.5,
            0.7,
            125,
            x_tilt=-8.0,
            y_tilt=14.0,
            rotation=50.0,
            tangential_pressure=0.5,
            speed=0.6,
        ),
    )
    replay = replayer(
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=ImmediateScheduler(),
        synthetic_input=synthetic_input,
    )

    replay.replay(
        FakeDocument(), node, session_for(samples), CONTEXT, lambda: None, lambda error: None
    )

    assert synthetic_input.strokes == []
    assert node.lines == []
    first, second = node.polyline_strokes[0]
    assert first.position == (1.25, 2.5)
    assert first.pressure == 0.2
    assert first.x_tilt == -10.0
    assert first.y_tilt == 12.0
    assert first.rotation == 45.0
    assert first.tangential_pressure == 0.3
    assert first.time == 0.0
    assert first.speed == 0.4
    assert second.time == 25.0
    assert second.speed == 0.6
    assert "replay-api api=paintPolylineStroke" in caplog.text
    assert "replay-stroke index=1 samples=2 pressure_min=0.20000 pressure_max=0.70000" in (
        caplog.text
    )


def test_synthetic_fallback_finishes_each_stroke_before_changing_settings() -> None:
    node = FakeNode()
    synthetic_input = FakeSyntheticInput()
    first = (BrushSample(1.0, 2.0, 0.2, 0), BrushSample(3.0, 4.0, 0.7, 10))
    second = (BrushSample(20.0, 21.0, 0.4, 20),)
    second_state = replace(BRUSH_STATE, size=25.0)
    session = RecordingSession(CONTEXT, (Stroke(first, BRUSH_STATE), Stroke(second, second_state)))
    document = FakeDocument()
    document.barrier_results = [False, True, True]
    scheduler = QueuedScheduler()
    completed = []
    applied = []
    errors = []

    replayer(scheduler=scheduler, synthetic_input=synthetic_input).replay(
        document,
        node,
        session,
        CONTEXT,
        lambda: completed.append(True),
        errors.append,
        apply_brush_state=applied.append,
    )
    while document.barrier_checks == 0:
        scheduler.run_next()

    assert applied == [BRUSH_STATE]
    assert synthetic_input.strokes == [(synthetic_input.target, first)]
    assert completed == []

    while scheduler.callbacks:
        scheduler.run_next()

    assert applied == [BRUSH_STATE, second_state]
    assert node.lines == []
    assert synthetic_input.strokes == [
        (synthetic_input.target, first),
        (synthetic_input.target, second),
    ]
    assert completed == [True]
    assert errors == []


def test_continuous_replay_applies_settings_before_each_stroke() -> None:
    node = ContinuousNode()
    second_state = replace(BRUSH_STATE, size=25.0)
    session = RecordingSession(
        CONTEXT,
        (
            Stroke((BrushSample(1.0, 1.0, 0.5, 0),), BRUSH_STATE),
            Stroke((BrushSample(2.0, 2.0, 0.5, 10),), second_state),
        ),
    )
    applied = []

    replayer(
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=ImmediateScheduler(),
    ).replay(
        FakeDocument(),
        node,
        session,
        CONTEXT,
        lambda: None,
        lambda error: None,
        apply_brush_state=applied.append,
    )

    assert applied == [BRUSH_STATE, second_state]
    assert len(node.polyline_strokes) == 2


def test_continuous_replay_yields_after_applying_settings() -> None:
    node = ContinuousNode()
    scheduler = QueuedScheduler()
    applied = []
    replay = replayer(
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=scheduler,
    )

    replay.replay(
        FakeDocument(),
        node,
        session_for((BrushSample(1.0, 2.0, 0.5, 0),)),
        CONTEXT,
        lambda: None,
        lambda error: None,
        apply_brush_state=applied.append,
    )
    scheduler.run_next()

    assert applied == [BRUSH_STATE]
    assert node.polyline_strokes == []

    scheduler.run_next()

    assert len(node.polyline_strokes) == 1


def test_replay_completion_is_polled_without_blocking() -> None:
    node = ContinuousNode()
    document = FakeDocument()
    document.barrier_results = [False, True]
    scheduler = QueuedScheduler()
    replay = replayer(
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=scheduler,
    )
    completed: list[bool] = []

    replay.replay(
        document,
        node,
        session_for((BrushSample(1.0, 2.0, 0.5, 0),)),
        CONTEXT,
        lambda: completed.append(True),
        lambda error: None,
    )
    while document.barrier_checks == 0:
        first_poll_delay = scheduler.run_next()

    assert first_poll_delay == 0
    assert completed == []
    assert scheduler.run_next() == 10
    assert completed == [True]
    assert document.unlocks == 1


def test_replay_completion_fails_if_context_changes_while_polling() -> None:
    node = ContinuousNode()
    document = FakeDocument()
    document.barrier_results = [False]
    scheduler = QueuedScheduler()
    active_context = [CONTEXT]
    replay = KritaReplayer(
        lambda: active_context[0],
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=scheduler,
    )
    completed: list[bool] = []
    errors: list[Exception] = []

    replay.replay(
        document,
        node,
        session_for((BrushSample(1.0, 2.0, 0.5, 0),)),
        CONTEXT,
        lambda: completed.append(True),
        errors.append,
    )
    while document.barrier_checks == 0:
        scheduler.run_next()
    active_context[0] = ContextRef("other-document", "other-layer")
    scheduler.run_next()

    assert completed == []
    assert len(errors) == 1
    assert "changed" in str(errors[0])
    assert document.barrier_checks == 1


def test_replay_completion_times_out(monkeypatch) -> None:
    node = ContinuousNode()
    document = FakeDocument()
    document.barrier_results = [False]
    scheduler = QueuedScheduler()
    clock_values = iter((0.0, 121.0))
    monkeypatch.setattr(
        "krita_brush_replay.infrastructure.krita_replay.monotonic",
        lambda: next(clock_values),
    )
    replay = replayer(
        float_point_factory=point_factory,
        stroke_point_factory=FakeStrokePoint,
        scheduler=scheduler,
    )
    completed: list[bool] = []
    errors: list[Exception] = []

    replay.replay(
        document,
        node,
        session_for((BrushSample(1.0, 2.0, 0.5, 0),)),
        CONTEXT,
        lambda: completed.append(True),
        errors.append,
    )
    while scheduler.callbacks:
        scheduler.run_next()

    assert completed == []
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
