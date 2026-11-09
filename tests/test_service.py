from collections.abc import Callable

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import (
    BrushSample,
    ContextRef,
    InputPhase,
    RecordingSession,
)

CONTEXT = ContextRef(document_id="document", layer_id="layer")
OTHER_LAYER = ContextRef(document_id="document", layer_id="other-layer")


class FakeHost:
    def __init__(self) -> None:
        self.context: ContextRef | None = CONTEXT
        self.context_calls = 0
        self.replayed: list[tuple[RecordingSession, ContextRef]] = []
        self.complete_replay_immediately = True
        self._on_complete: Callable[[], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None

    def active_context(self) -> ContextRef | None:
        self.context_calls += 1
        return self.context

    def replay(
        self,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.replayed.append((session, target))
        self._on_complete = on_complete
        self._on_error = on_error
        if self.complete_replay_immediately:
            on_complete()


def sample(x: float, pressure: float = 0.5, timestamp_ms: int = 0) -> BrushSample:
    return BrushSample(x=x, y=4.0, pressure=pressure, timestamp_ms=timestamp_ms)


def record_one_stroke(service: BrushReplayService) -> None:
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0, timestamp_ms=10))
    service.accept_input(InputPhase.MOVE, sample(2.0, timestamp_ms=20))
    service.accept_input(InputPhase.RELEASE, None)
    service.stop_recording()


def test_service_records_complete_stroke() -> None:
    service = BrushReplayService(FakeHost())

    record_one_stroke(service)

    assert service.state.mode is ServiceMode.IDLE
    assert service.state.stroke_count == 1
    assert service.session is not None
    assert [point.x for point in service.session.strokes[0].samples] == [1.0, 2.0]


def test_start_recording_replaces_previous_session() -> None:
    service = BrushReplayService(FakeHost())
    record_one_stroke(service)

    service.start_recording()

    assert service.state.mode is ServiceMode.RECORDING
    assert service.state.stroke_count == 0


def test_context_change_on_release_discards_the_active_stroke() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0))
    service.accept_input(InputPhase.MOVE, sample(2.0))
    host.context = OTHER_LAYER

    service.accept_input(InputPhase.RELEASE, None)

    assert service.state.mode is ServiceMode.IDLE
    assert "layer changed" in service.state.message.lower()
    assert service.session is not None
    assert service.session.strokes == ()


def test_move_samples_do_not_query_krita_context() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0))
    calls_after_press = host.context_calls

    service.accept_input(InputPhase.MOVE, sample(2.0))
    service.accept_input(InputPhase.MOVE, sample(3.0))

    assert host.context_calls == calls_after_press


def test_move_samples_do_not_notify_the_ui() -> None:
    service = BrushReplayService(FakeHost())
    states = []
    service.subscribe(states.append)
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0))
    notification_count = len(states)

    service.accept_input(InputPhase.MOVE, sample(2.0))
    service.accept_input(InputPhase.MOVE, sample(3.0))

    assert len(states) == notification_count


def test_unsubscribed_listener_receives_no_more_state_changes() -> None:
    service = BrushReplayService(FakeHost())
    states = []
    service.subscribe(states.append)

    service.unsubscribe(states.append)
    service.start_recording()

    assert len(states) == 1


def test_canvas_change_stops_recording() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    service.start_recording()
    host.context = OTHER_LAYER

    service.canvas_changed()

    assert service.state.mode is ServiceMode.IDLE
    assert "layer changed" in service.state.message.lower()


def test_capture_failure_stops_recording_and_discards_partial_stroke() -> None:
    service = BrushReplayService(FakeHost())
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0))

    service.capture_failed(RuntimeError("tablet failed"))

    assert service.state.mode is ServiceMode.IDLE
    assert "tablet failed" in service.state.message
    assert service.session is not None
    assert service.session.strokes == ()


def test_replay_uses_origin_as_the_initial_target() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_one_stroke(service)

    service.replay()

    assert service.session is not None
    assert host.replayed == [(service.session, service.session.origin)]
    assert service.state.mode is ServiceMode.IDLE
    assert "complete" in service.state.message.lower()


def test_replay_rejects_a_different_active_layer() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_one_stroke(service)
    host.context = OTHER_LAYER

    service.replay()

    assert host.replayed == []
    assert "same document and layer" in service.state.message.lower()


def test_replay_without_strokes_is_a_silent_no_op() -> None:
    service = BrushReplayService(FakeHost())
    original_message = service.state.message

    service.replay()

    assert service.state.message == original_message
    assert not service.state.can_replay


def test_replay_reports_replaying_until_async_host_finishes() -> None:
    host = FakeHost()
    host.complete_replay_immediately = False
    service = BrushReplayService(host)
    record_one_stroke(service)

    service.replay()

    assert service.state.mode is ServiceMode.REPLAYING
    assert host._on_complete is not None
    host._on_complete()
    assert service.state.mode is ServiceMode.IDLE


def test_replay_returns_to_idle_after_async_failure() -> None:
    host = FakeHost()
    host.complete_replay_immediately = False
    service = BrushReplayService(host)
    record_one_stroke(service)
    service.replay()

    assert host._on_error is not None
    host._on_error(TimeoutError("Krita did not finish replay in time"))

    assert service.state.mode is ServiceMode.IDLE
    assert "did not finish" in service.state.message
