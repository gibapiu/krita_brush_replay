from collections.abc import Callable

from krita_brush_replay.domain.recording import (
    BrushSample,
    ContextRef,
    RecordingSession,
    Stroke,
)
from krita_brush_replay.infrastructure.krita_replay import (
    KritaReplayer,
    _create_qpoint,
)

CONTEXT = ContextRef("document", "layer")


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


def line_factories() -> tuple[tuple[str, Callable[[float, float], object]], ...]:
    return (("QPointF", point_factory),)


def session_for(*strokes: tuple[BrushSample, ...]) -> RecordingSession:
    return RecordingSession(CONTEXT, tuple(Stroke(samples) for samples in strokes))


def replayer(**kwargs) -> KritaReplayer:
    return KritaReplayer(lambda: CONTEXT, **kwargs)


class FakeQPoint:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class FakeQPointF:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class StrictQPointNode(FakeNode):
    def paintLine(self, start, end, start_pressure, end_pressure) -> None:
        if not isinstance(start, FakeQPoint) or not isinstance(end, FakeQPoint):
            raise TypeError("paintLine argument 1 has unexpected type")
        super().paintLine(start, end, start_pressure, end_pressure)


StrictQPointNode.paintLine.__doc__ = "paintLine(self, pointOne: QPoint, pointTwo: QPoint)"


class MisreportedQPointFNode(FakeNode):
    def __init__(self) -> None:
        super().__init__()
        self.rejected_calls = 0

    def paintLine(self, start, end, start_pressure, end_pressure) -> None:
        if not isinstance(start, FakeQPointF) or not isinstance(end, FakeQPointF):
            self.rejected_calls += 1
            raise TypeError("paintLine argument 1 has unexpected type")
        super().paintLine(start, end, start_pressure, end_pressure)


MisreportedQPointFNode.paintLine.__doc__ = (
    "paintLine(self, pointOne: QPoint, pointTwo: QPoint)"
)


class QualifiedQPointFNode(FakeNode):
    def __init__(self) -> None:
        super().__init__()
        self.rejected_calls = 0

    def paintLine(self, start, end, start_pressure, end_pressure) -> None:
        if not isinstance(start, FakeQPointF) or not isinstance(end, FakeQPointF):
            self.rejected_calls += 1
            raise TypeError("paintLine argument 1 has unexpected type")
        super().paintLine(start, end, start_pressure, end_pressure)


QualifiedQPointFNode.paintLine.__doc__ = (
    "paintLine(self, pointOne: PyQt6.QtCore.QPointF, "
    "pointTwo: PyQt6.QtCore.QPointF)"
)


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


def test_qpoint_conversion_uses_integer_coordinates() -> None:
    point = _create_qpoint(FakeQPoint, 12.6, -4.4)

    assert isinstance(point, FakeQPoint)
    assert (point.x, point.y) == (13, -4)


def test_released_binding_uses_qpoint_from_bound_signature() -> None:
    node = StrictQPointNode()
    samples = (
        BrushSample(1.2, 2.8, 0.2, 0),
        BrushSample(3.4, 4.6, 0.7, 10),
    )
    replay = replayer(
        point_factories=(
            ("QPoint", lambda x, y: FakeQPoint(round(x), round(y))),
            ("QPointF", FakeQPointF),
        ),
        scheduler=ImmediateScheduler(),
    )

    replay.replay(
        FakeDocument(), node, session_for(samples), CONTEXT, lambda: None, lambda error: None
    )

    start, end, _, _ = node.lines[0]
    assert isinstance(start, FakeQPoint)
    assert (start.x, start.y) == (1, 3)
    assert (end.x, end.y) == (3, 5)


def test_paint_line_retries_and_caches_alternate_point_binding() -> None:
    node = MisreportedQPointFNode()
    samples = (
        BrushSample(1.25, 2.5, 0.2, 0),
        BrushSample(3.75, 4.5, 0.7, 10),
        BrushSample(5.25, 6.5, 0.4, 20),
    )
    replay = replayer(
        point_factories=(
            ("QPoint", lambda x, y: FakeQPoint(round(x), round(y))),
            ("QPointF", FakeQPointF),
        ),
        scheduler=ImmediateScheduler(),
    )

    replay.replay(
        FakeDocument(), node, session_for(samples), CONTEXT, lambda: None, lambda error: None
    )

    assert node.rejected_calls == 1
    assert len(node.lines) == 2
    assert isinstance(node.lines[0][0], FakeQPointF)


def test_paint_line_prefers_qpointf_from_qualified_bound_signature() -> None:
    node = QualifiedQPointFNode()
    samples = (
        BrushSample(1.25, 2.5, 0.2, 0),
        BrushSample(3.75, 4.5, 0.7, 10),
    )
    replay = replayer(
        point_factories=(
            ("QPoint", lambda x, y: FakeQPoint(round(x), round(y))),
            ("QPointF", FakeQPointF),
        ),
        scheduler=ImmediateScheduler(),
    )

    replay.replay(
        FakeDocument(), node, session_for(samples), CONTEXT, lambda: None, lambda error: None
    )

    assert node.rejected_calls == 0
    assert isinstance(node.lines[0][0], FakeQPointF)
    assert node.lines[0][0].x == 1.25


def test_continuous_api_receives_all_recorded_input_axes() -> None:
    node = ContinuousNode()
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
    )

    replay.replay(
        FakeDocument(), node, session_for(samples), CONTEXT, lambda: None, lambda error: None
    )

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


def test_replay_paints_pressure_aware_segments_in_batches() -> None:
    node = FakeNode()
    document = FakeDocument()
    replay = replayer(
        point_factories=line_factories(),
        scheduler=ImmediateScheduler(),
        replay_batch_size=1,
    )
    samples = (
        BrushSample(1.0, 2.0, 0.2, 0),
        BrushSample(3.0, 4.0, 0.7, 10),
        BrushSample(5.0, 6.0, 0.0, 20),
    )
    completed: list[bool] = []
    errors: list[Exception] = []

    replay.replay(
        document,
        node,
        session_for(samples),
        CONTEXT,
        lambda: completed.append(True),
        errors.append,
    )

    assert node.lines == [
        ((1.0, 2.0), (3.0, 4.0), 0.2, 0.7),
        ((3.0, 4.0), (5.0, 6.0), 0.7, 0.0),
    ]
    assert document.barrier_checks == 1
    assert document.unlocks == 1
    assert completed == [True]
    assert errors == []


def test_line_replay_does_not_join_separate_recorded_strokes() -> None:
    node = FakeNode()
    replay = replayer(
        point_factories=line_factories(),
        scheduler=ImmediateScheduler(),
    )
    first = (
        BrushSample(1.0, 2.0, 0.2, 0),
        BrushSample(3.0, 4.0, 0.7, 10),
    )
    second = (
        BrushSample(20.0, 21.0, 0.4, 20),
        BrushSample(22.0, 23.0, 0.6, 30),
    )

    replay.replay(
        FakeDocument(),
        node,
        session_for(first, second),
        CONTEXT,
        lambda: None,
        lambda error: None,
    )

    assert [(start, end) for start, end, _p1, _p2 in node.lines] == [
        ((1.0, 2.0), (3.0, 4.0)),
        ((20.0, 21.0), (22.0, 23.0)),
    ]


def test_replay_completion_is_polled_without_blocking() -> None:
    node = FakeNode()
    document = FakeDocument()
    document.barrier_results = [False, True]
    scheduler = QueuedScheduler()
    replay = replayer(
        point_factories=line_factories(),
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
    scheduler.run_next()
    first_poll_delay = scheduler.run_next()

    assert first_poll_delay == 0
    assert completed == []
    assert scheduler.run_next() == 10
    assert completed == [True]
    assert document.unlocks == 1


def test_replay_completion_fails_if_context_changes_while_polling() -> None:
    node = FakeNode()
    document = FakeDocument()
    document.barrier_results = [False]
    scheduler = QueuedScheduler()
    active_context = [CONTEXT]
    replay = KritaReplayer(
        lambda: active_context[0],
        point_factories=line_factories(),
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
    scheduler.run_next()
    active_context[0] = ContextRef("other-document", "other-layer")
    scheduler.run_next()

    assert completed == []
    assert len(errors) == 1
    assert "changed" in str(errors[0])
    assert document.barrier_checks == 0


def test_replay_completion_times_out(monkeypatch) -> None:
    node = FakeNode()
    document = FakeDocument()
    document.barrier_results = [False]
    scheduler = QueuedScheduler()
    clock_values = iter((0.0, 121.0))
    monkeypatch.setattr(
        "krita_brush_replay.infrastructure.krita_replay.monotonic",
        lambda: next(clock_values),
    )
    replay = replayer(
        point_factories=line_factories(),
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
    scheduler.run_next()
    scheduler.run_next()

    assert completed == []
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)


def test_replay_of_a_tap_paints_a_zero_length_line() -> None:
    node = FakeNode()
    replay = replayer(
        point_factories=line_factories(),
        scheduler=ImmediateScheduler(),
    )
    point = BrushSample(7.0, 8.0, 0.9, 0)

    replay.replay(
        FakeDocument(),
        node,
        session_for((point,)),
        CONTEXT,
        lambda: None,
        lambda error: None,
    )

    assert node.lines == [((7.0, 8.0), (7.0, 8.0), 0.9, 0.9)]
