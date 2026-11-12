import pytest
from input_support import FakeEvent, FakeHost, TabletEvent, UnmarkedMouseEvent, mouse_event

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import BrushSample, ContextRef, InputPhase
from krita_brush_replay.infrastructure.input_events import event_type, is_synthesized_mouse
from krita_brush_replay.infrastructure.input_filter import CanvasInputFilter


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
    event = FakeEvent(event_type("MouseMove"), 1.0, 2.0, 10)
    event.source = lambda: 1

    assert is_synthesized_mouse(event)


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
        event = UnmarkedMouseEvent(event_type(name), 2.0, 4.0, timestamp)
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

    send_mouse("MouseButtonPress", 50)
    send_mouse("MouseMove", 60)
    send_mouse("MouseButtonRelease", 70)
    assert [sample.pressure for _phase, sample in captured[-3:] if sample] == [1.0, 1.0]


def test_detaching_capture_clears_tablet_ownership() -> None:
    canvas = object()
    captured = []
    input_filter = CanvasInputFilter(
        FakeHost(canvas), lambda *args: captured.append(args), lambda error: None
    )
    input_filter._handle(TabletEvent("TabletPress", 0.2, 10), canvas, "tablet")

    input_filter.reset()
    input_filter._handle(mouse_event("MouseButtonPress", 1.0, 20), canvas, "mouse")

    assert captured[-1][0] is InputPhase.PRESS
    assert captured[-1][1].pressure == 1.0
