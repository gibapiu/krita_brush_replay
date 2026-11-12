import logging

import pytest

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import BrushSample, ContextRef, InputPhase
from krita_brush_replay.infrastructure.input_filter import (
    CanvasInputFilter,
    _event_type,
    _is_synthesized_mouse,
    _left_button,
)
from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum


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


class FailingBrushStateHost(FakeHost):
    def active_context(self) -> ContextRef:
        return ContextRef("document", "layer")

    def capture_brush_state(self):
        raise RuntimeError("active brush settings are unavailable")


class PressFilter(CanvasInputFilter):
    def _dispatch(self, watched, event) -> None:
        self._handler(
            InputPhase.PRESS,
            BrushSample(1.0, 2.0, 0.5, 0),
        )


def test_brush_state_capture_failure_stops_recording_with_a_useful_message() -> None:
    host = FailingBrushStateHost(object())
    service = BrushReplayService(host)
    service.start_recording()
    input_filter = PressFilter(
        host,
        service.accept_input,
        service.capture_failed,
    )

    input_filter.eventFilter(object(), object())

    assert service.state.mode is ServiceMode.IDLE
    assert service.state.message == ("Recording stopped: active brush settings are unavailable")
    assert service.session is not None
    assert service.session.strokes == ()


def test_synthesized_mouse_event_is_rejected() -> None:
    event = FakeEvent(_event_type("MouseMove"), 1.0, 2.0, 10)
    event.source = lambda: 1

    assert _is_synthesized_mouse(event)


class TabletEvent(FakeEvent):
    def __init__(self, name: str, pressure: float, timestamp: int) -> None:
        super().__init__(_event_type(name), float(timestamp), 4.0, timestamp)
        self._pressure = pressure

    def pressure(self) -> float:
        return self._pressure


class UnmarkedMouseEvent(FakeEvent):
    def source(self):
        # OS-generated pen mouse events can claim to be real mouse input.
        return qt_enum(QtCore.Qt, "MouseEventSource", "MouseEventNotSynthesized")


@pytest.mark.parametrize("mouse_release_first", [True, False])
def test_unmarked_mouse_events_cannot_take_over_a_tablet_stroke(
    monkeypatch, mouse_release_first
) -> None:
    from krita_brush_replay.infrastructure.qt_facade import QtGui

    monkeypatch.setattr(QtGui, "QTabletEvent", TabletEvent)
    monkeypatch.setattr(QtGui, "QMouseEvent", UnmarkedMouseEvent)
    canvas = object()
    host = FakeHost(canvas)
    captured = []
    errors = []
    input_filter = CanvasInputFilter(host, lambda *args: captured.append(args), errors.append)

    def send_mouse(name: str, timestamp: int) -> None:
        event = UnmarkedMouseEvent(_event_type(name), 2.0, 4.0, timestamp)
        assert not input_filter.eventFilter(canvas, event)

    assert not input_filter.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))
    send_mouse("MouseButtonPress", 11)
    send_mouse("MouseMove", 12)
    assert not input_filter.eventFilter(canvas, TabletEvent("TabletMove", 0.5, 20))
    if mouse_release_first:
        send_mouse("MouseButtonRelease", 21)
    assert not input_filter.eventFilter(canvas, TabletEvent("TabletMove", 0.8, 30))
    assert not input_filter.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 40))
    if not mouse_release_first:
        send_mouse("MouseButtonRelease", 41)

    assert errors == []
    assert [phase for phase, _sample in captured] == [
        InputPhase.PRESS,
        InputPhase.MOVE,
        InputPhase.MOVE,
        InputPhase.RELEASE,
    ]
    assert [sample.pressure for _phase, sample in captured if sample] == [0.2, 0.5, 0.8]
    assert host.mapper_calls == 1

    # A real mouse gesture after the pen lifts must still be recorded normally.
    send_mouse("MouseButtonPress", 50)
    send_mouse("MouseMove", 60)
    send_mouse("MouseButtonRelease", 70)
    assert [sample.pressure for _phase, sample in captured[-3:] if sample] == [1.0, 1.0]


def test_capture_logs_pressure_and_ignored_mouse_counts_without_logging_every_move(caplog) -> None:
    canvas = object()
    input_filter = CanvasInputFilter(FakeHost(canvas), lambda *args: None, lambda error: None)
    caplog.set_level(logging.INFO, logger="krita_brush_replay.infrastructure.input_filter")

    input_filter._handle(TabletEvent("TabletPress", 0.2, 10), canvas, "tablet")
    for timestamp in range(11, 111):
        input_filter._handle(TabletEvent("TabletMove", 0.8, timestamp), canvas, "tablet")
        input_filter._handle(
            UnmarkedMouseEvent(_event_type("MouseButtonPress"), 1.0, 4.0, timestamp),
            canvas,
            "mouse",
        )
    input_filter._handle(TabletEvent("TabletRelease", 0.0, 111), canvas, "tablet")

    assert "samples=101 pressure_min=0.20000 pressure_max=0.80000" in caplog.text
    source = qt_enum(QtCore.Qt, "MouseEventSource", "MouseEventNotSynthesized")
    assert f"mouse:PRESS:tablet-active:source={source}': 100" in caplog.text
    assert caplog.text.count("capture-ignore") == 4
    assert len(caplog.records) == 7


def test_detaching_capture_clears_tablet_ownership() -> None:
    canvas = object()
    captured = []
    input_filter = CanvasInputFilter(
        FakeHost(canvas), lambda *args: captured.append(args), lambda error: None
    )
    input_filter._handle(TabletEvent("TabletPress", 0.2, 10), canvas, "tablet")

    input_filter.finish_capture()
    input_filter._handle(mouse_event("MouseButtonPress", 1.0, 20), canvas, "mouse")

    assert captured[-1][0] is InputPhase.PRESS
    assert captured[-1][1].pressure == 1.0
