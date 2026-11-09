from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.input_filter import (
    CanvasInputFilter,
    _event_type,
    _is_synthesized_mouse,
    _left_button,
)


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self._x = x
        self._y = y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class FakeEvent:
    def __init__(
        self,
        event_type,
        x: float,
        y: float,
        timestamp: int,
    ) -> None:
        self._event_type = event_type
        self._point = FakePoint(x, y)
        self._timestamp = timestamp

    def type(self):
        return self._event_type

    def button(self):
        return _left_button()

    def position(self) -> FakePoint:
        return self._point

    def timestamp(self) -> int:
        return self._timestamp


class FakeHost:
    def __init__(self, canvas_target) -> None:
        self.canvas_target = canvas_target
        self.mapper_calls = 0
        self.mapped_positions: list[tuple[float, float]] = []

    def is_canvas_target(self, target) -> bool:
        return target is self.canvas_target

    def capture_mapper(self):
        self.mapper_calls += 1

        def to_image(position: tuple[float, float]) -> tuple[float, float]:
            self.mapped_positions.append(position)
            return position[0] + 10.0, position[1] + 20.0

        return to_image


def mouse_event(name: str, x: float, timestamp: int) -> FakeEvent:
    return FakeEvent(_event_type(name), x, 4.0, timestamp)


def test_filter_tracks_one_target_and_caches_one_transform_per_stroke() -> None:
    canvas = object()
    other = object()
    host = FakeHost(canvas)
    captured = []
    input_filter = CanvasInputFilter(host, lambda *args: captured.append(args), lambda error: None)

    input_filter._handle(mouse_event("MouseButtonPress", 1.0, 10), canvas, "mouse")
    input_filter._handle(mouse_event("MouseMove", 2.0, 20), other, "mouse")
    input_filter._handle(mouse_event("MouseMove", 3.0, 30), canvas, "mouse")
    input_filter._handle(mouse_event("MouseButtonRelease", 4.0, 40), canvas, "mouse")
    input_filter._handle(mouse_event("MouseMove", 5.0, 50), canvas, "mouse")

    assert [phase for phase, _sample in captured] == [
        InputPhase.PRESS,
        InputPhase.MOVE,
        InputPhase.RELEASE,
    ]
    assert host.mapper_calls == 1
    assert [position[0] for position in host.mapped_positions] == [1.0, 3.0]
    assert [(sample.x, sample.y) for _phase, sample in captured[:-1]] == [
        (11.0, 24.0),
        (13.0, 24.0),
    ]
    assert captured[-1] == (InputPhase.RELEASE, None)


def test_filter_ignores_press_outside_the_canvas() -> None:
    canvas = object()
    host = FakeHost(canvas)
    captured = []
    input_filter = CanvasInputFilter(host, lambda *args: captured.append(args), lambda error: None)

    input_filter._handle(
        mouse_event("MouseButtonPress", 1.0, 10),
        object(),
        "mouse",
    )

    assert captured == []
    assert host.mapper_calls == 0


class FailingFilter(CanvasInputFilter):
    def _dispatch(self, watched, event) -> None:
        raise RuntimeError("capture failed")


def test_event_filter_contains_and_reports_callback_errors() -> None:
    errors = []
    input_filter = FailingFilter(FakeHost(object()), lambda *args: None, errors.append)

    consumed = input_filter.eventFilter(object(), object())

    assert not consumed
    assert len(errors) == 1
    assert str(errors[0]) == "capture failed"


def test_synthesized_mouse_event_is_rejected() -> None:
    event = FakeEvent(_event_type("MouseMove"), 1.0, 2.0, 10)
    event.source = lambda: 1

    assert _is_synthesized_mouse(event)
