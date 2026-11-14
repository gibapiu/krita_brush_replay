from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import BrushChoices, StrokeSettings
from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef
from krita_brush_replay.runtime import BrushReplayRuntime


class FakeApplication:
    def __init__(self) -> None:
        self.installed = []
        self.removed = []

    def installEventFilter(self, event_filter) -> None:
        self.installed.append(event_filter)

    def removeEventFilter(self, event_filter) -> None:
        self.removed.append(event_filter)


class FakePanel:
    def __init__(self) -> None:
        self.states = []
        self.choices = []
        self.messages = []

    def apply_state(self, state: ServiceState) -> None:
        self.states.append(state)

    def set_choices(self, choices: BrushChoices) -> None:
        self.choices.append(choices)

    def show_message(self, message: str) -> None:
        self.messages.append(message)


class FakeHost:
    def __init__(self, choices: BrushChoices | Exception = BrushChoices()) -> None:
        self._choices = choices
        self.invalidations = 0

    def invalidate_canvas_target(self) -> None:
        self.invalidations += 1

    def brush_choices(self) -> BrushChoices:
        if isinstance(self._choices, Exception):
            raise self._choices
        return self._choices


RECORDED_STROKE = StrokeSettings(
    BrushState(
        preset=ResourceRef("preset", "Ink", "ink.kpp"),
        foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
        background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
        size=10.0,
        opacity=1.0,
        flow=1.0,
        rotation=0.0,
        pattern=ResourceRef("pattern", "Paper", "paper.pat"),
        gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
        blending_mode="normal",
    )
)


class FakeService:
    def __init__(self) -> None:
        self.unsubscribed = []
        self.canvas_changes = 0

    def canvas_changed(self) -> None:
        self.canvas_changes += 1

    def unsubscribe(self, listener) -> None:
        self.unsubscribed.append(listener)


def state(mode: ServiceMode) -> ServiceState:
    return ServiceState(mode, (), 0, "")


def runtime_without_qt_construction() -> BrushReplayRuntime:
    runtime = object.__new__(BrushReplayRuntime)
    runtime._panel = FakePanel()
    runtime._host = FakeHost()
    runtime._application = FakeApplication()
    runtime._input_filter = FakeInputFilter()
    runtime._filter_installed = False
    runtime._mode = ServiceMode.IDLE
    runtime._closed = False
    runtime._service = FakeService()
    return runtime


def test_runtime_installs_filter_only_while_recording() -> None:
    runtime = runtime_without_qt_construction()

    runtime._apply_state(state(ServiceMode.IDLE))
    runtime._apply_state(state(ServiceMode.RECORDING))
    runtime._apply_state(state(ServiceMode.RECORDING))
    runtime._apply_state(state(ServiceMode.IDLE))

    assert runtime._application.installed == [runtime._input_filter]
    assert runtime._application.removed == [runtime._input_filter]


def test_runtime_close_removes_an_installed_filter_once() -> None:
    runtime = runtime_without_qt_construction()
    runtime._set_filter_installed(True)

    runtime.close()
    runtime.close()

    assert runtime._application.removed == [runtime._input_filter]
    assert runtime._service.unsubscribed == [runtime._apply_state]


def test_runtime_ignores_service_callbacks_after_close() -> None:
    runtime = runtime_without_qt_construction()
    runtime.close()

    runtime._apply_state(state(ServiceMode.REPLAYING))

    assert runtime._panel.states == []


def test_runtime_invalidates_replay_canvas_on_switch_and_close() -> None:
    runtime = runtime_without_qt_construction()

    runtime.canvas_changed()

    assert runtime._host.invalidations == 1
    assert runtime._service.canvas_changes == 1

    runtime.close()

    assert runtime._host.invalidations == 2


def test_finishing_a_recording_loads_brush_choices_once() -> None:
    runtime = runtime_without_qt_construction()
    choices = BrushChoices(presets=(ResourceRef("preset", "Pencil", "pencil.kpp"),))
    runtime._host = FakeHost(choices)
    recorded = ServiceState(ServiceMode.IDLE, (RECORDED_STROKE,), 1, "")

    runtime._apply_state(state(ServiceMode.RECORDING))
    runtime._apply_state(recorded)
    runtime._apply_state(recorded)

    assert runtime._panel.choices == [choices]


def test_unavailable_brush_choices_are_reported() -> None:
    runtime = runtime_without_qt_construction()
    runtime._host = FakeHost(RuntimeError("resources locked"))

    runtime._apply_state(state(ServiceMode.RECORDING))
    runtime._apply_state(ServiceState(ServiceMode.IDLE, (RECORDED_STROKE,), 1, ""))

    assert runtime._panel.choices == []
    assert runtime._panel.messages == ["Could not list brush resources: resources locked"]


class FakeInputFilter:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


def test_detaching_filter_resets_gesture_ownership() -> None:
    runtime = runtime_without_qt_construction()
    runtime._set_filter_installed(True)

    runtime._set_filter_installed(False)

    assert runtime._input_filter.resets == 1
