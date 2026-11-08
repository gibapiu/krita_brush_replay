from krita_brush_replay.app.service import ServiceMode, ServiceState
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

    def apply_state(self, state: ServiceState) -> None:
        self.states.append(state)


class FakeService:
    def __init__(self) -> None:
        self.unsubscribed = []

    def unsubscribe(self, listener) -> None:
        self.unsubscribed.append(listener)


def state(mode: ServiceMode) -> ServiceState:
    return ServiceState(mode, 0, 0, "")


def runtime_without_qt_construction() -> BrushReplayRuntime:
    runtime = object.__new__(BrushReplayRuntime)
    runtime._panel = FakePanel()
    runtime._application = FakeApplication()
    runtime._input_filter = object()
    runtime._filter_installed = False
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
