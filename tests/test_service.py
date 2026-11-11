from collections.abc import Callable

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    InputPhase,
    RecordingSession,
    ResourceRef,
)

CONTEXT = ContextRef(document_id="document", layer_id="layer")
OTHER_LAYER = ContextRef(document_id="document", layer_id="other-layer")


def brush_state(size: float = 10.0) -> BrushState:
    return BrushState(
        preset=ResourceRef("preset", f"Brush {size}", f"brush-{size}.kpp"),
        foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
        background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
        size=size,
        opacity=1.0,
        flow=1.0,
        rotation=0.0,
        pattern=ResourceRef("pattern", "Canvas", "canvas.pat"),
        gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
        blending_mode="normal",
    )


class FakeHost:
    def __init__(self) -> None:
        self.context: ContextRef | None = CONTEXT
        self.context_calls = 0
        self.replayed: list[tuple[RecordingSession, ContextRef]] = []
        self.brush_state = brush_state()
        self.brush_state_captures = 0
        self.replay_original_settings: list[bool] = []
        self.complete_replay_immediately = True
        self._on_complete: Callable[[], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None

    def active_context(self) -> ContextRef | None:
        self.context_calls += 1
        return self.context

    def capture_brush_state(self) -> BrushState:
        self.brush_state_captures += 1
        return self.brush_state

    def replay(
        self,
        session: RecordingSession,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        use_original_settings: bool,
    ) -> None:
        if self.context is None:
            on_error(RuntimeError("select a writable paint layer first"))
            return
        self.replayed.append((session, self.context))
        self.replay_original_settings.append(use_original_settings)
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
    assert service.session.strokes[0].brush_state == brush_state()


def test_each_stroke_keeps_the_settings_captured_when_it_started() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    service.start_recording()
    service.accept_input(InputPhase.PRESS, sample(1.0))
    service.accept_input(InputPhase.RELEASE, None)
    host.brush_state = brush_state(25.0)
    service.accept_input(InputPhase.PRESS, sample(2.0))
    service.accept_input(InputPhase.RELEASE, None)
    service.stop_recording()

    assert service.session is not None
    assert [stroke.brush_state.size for stroke in service.session.strokes] == [10.0, 25.0]


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
    assert host.brush_state_captures == 1


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


def test_replay_uses_the_active_layer_as_the_target() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_one_stroke(service)
    host.context = OTHER_LAYER

    service.replay()

    assert service.session is not None
    assert host.replayed == [(service.session, OTHER_LAYER)]
    assert host.replay_original_settings == [True]
    assert service.state.mode is ServiceMode.IDLE
    assert "complete" in service.state.message.lower()


def test_current_settings_replay_is_an_explicit_comparison_mode() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_one_stroke(service)

    service.replay_with_current_settings()

    assert host.replay_original_settings == [False]


def test_replay_reports_when_there_is_no_active_paint_layer() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_one_stroke(service)
    host.context = None

    service.replay()

    assert host.replayed == []
    assert "select a writable paint layer" in service.state.message.lower()


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


def record_strokes(service: BrushReplayService, count: int) -> None:
    service.start_recording()
    for index in range(count):
        service.accept_input(InputPhase.PRESS, sample(float(index)))
        service.accept_input(InputPhase.RELEASE, None)
    service.stop_recording()


def replayed_sizes(host: FakeHost) -> list[float]:
    session, _target = host.replayed[-1]
    return [stroke.brush_state.size for stroke in session.strokes]


def test_override_changes_only_selected_strokes_and_keeps_the_recording() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_strokes(service, 3)

    service.override_setting((1,), "size", 30.0)
    service.replay()

    assert replayed_sizes(host) == [10.0, 30.0, 10.0]
    assert service.session is not None
    assert [stroke.brush_state.size for stroke in service.session.strokes] == [10.0] * 3
    assert [stroke.effective.size for stroke in service.state.strokes] == [10.0, 30.0, 10.0]


def test_choosing_the_recorded_value_again_clears_the_override() -> None:
    host = FakeHost()
    host.brush_state = brush_state(12.345)
    service = BrushReplayService(host)
    record_strokes(service, 1)
    service.override_setting((0,), "size", 12.36)

    service.override_setting((0,), "size", 12.35)

    assert not service.state.strokes[0].override.overridden_settings


def test_current_settings_comparison_ignores_overrides() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_strokes(service, 1)
    service.override_setting((0,), "size", 30.0)

    service.replay_with_current_settings()

    assert replayed_sizes(host) == [10.0]


def test_invalid_override_is_reported_and_not_applied() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_strokes(service, 2)

    service.override_setting((0, 1), "size", 0.0)
    service.override_setting((5,), "size", 20.0)
    service.replay()

    assert replayed_sizes(host) == [10.0, 10.0]
    assert not any(stroke.override.overridden_settings for stroke in service.state.strokes)


def test_new_recording_discards_previous_overrides() -> None:
    host = FakeHost()
    service = BrushReplayService(host)
    record_strokes(service, 1)
    service.override_setting((0,), "size", 30.0)

    record_strokes(service, 1)
    service.replay()

    assert replayed_sizes(host) == [10.0]


def test_settings_cannot_change_while_replaying() -> None:
    host = FakeHost()
    host.complete_replay_immediately = False
    service = BrushReplayService(host)
    record_strokes(service, 1)
    service.replay()

    service.override_setting((0,), "size", 30.0)

    assert not service.state.strokes[0].override.overridden_settings
