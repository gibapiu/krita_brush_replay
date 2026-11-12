from types import SimpleNamespace

import pytest

from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.infrastructure.qt_facade import QtWidgets

pytestmark = pytest.mark.skipif(
    not hasattr(QtWidgets, "QGroupBox"), reason="A real Qt binding is not installed"
)


@pytest.fixture
def controller():
    from krita_brush_replay.observability.controller import DiagnosticController

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    krita = SimpleNamespace(
        version=lambda: "5.4-test",
        readSetting=lambda group, key, default: default,
        action=lambda name: None,
    )
    instance = DiagnosticController(krita)
    yield instance
    instance.controls.deleteLater()
    application.processEvents()


def test_controls_toggle_output_and_collection_independently(controller) -> None:
    output, collection = controller.controls.findChildren(QtWidgets.QCheckBox)

    output.setChecked(False)

    assert not controller.monitor.output_enabled
    assert controller.monitor.collection_enabled
    collection.setChecked(False)
    assert not controller.monitor.enabled
    output.setChecked(True)
    assert controller.monitor.output_enabled
    assert not controller.monitor.collection_enabled


def test_export_dialog_handles_cancel_and_only_opens_while_idle(controller, monkeypatch) -> None:
    requests = []
    controls = controller.controls
    controls.export_requested.connect(requests.append)
    button = controls.findChild(QtWidgets.QPushButton)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *args: ("/tmp/test-report.txt", "Text reports (*.txt)"),
    )
    monkeypatch.setattr(controller._writer, "export", lambda *args: None)

    button.click()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    button.click()

    assert requests == ["/tmp/test-report.txt"]
    for mode, enabled in (
        (ServiceMode.RECORDING, False),
        (ServiceMode.REPLAYING, False),
        (ServiceMode.IDLE, True),
    ):
        controller.state_changed(ServiceState(mode, (), 0, ""))
        assert button.isEnabled() is enabled


def test_export_snapshots_state_and_monitor_metrics_before_background_writing(controller) -> None:
    class Writer:
        def export(self, path, report):
            self.path = path
            self.report = report

    writer = Writer()
    controller._writer = writer
    controller.state_changed(ServiceState(ServiceMode.IDLE, (), 0, "Ready"))

    controller.export("report.txt")

    assert writer.path == "report.txt"
    assert '"krita_version": "5.4-test"' in writer.report
    assert '"service_mode": "IDLE"' in writer.report
    assert '"observation_work_ms":' in writer.report
    assert '"observation_failures": 0' in writer.report


def test_export_failure_is_confined_to_the_diagnostic_controls(controller) -> None:
    class Writer:
        def export(self, path, report):
            raise OSError("cannot start writer")

    controller._writer = Writer()

    controller.export("report.txt")

    assert controller.controls.findChild(QtWidgets.QLabel).text() == (
        "Could not export diagnostic report: cannot start writer"
    )


def test_broken_diagnostic_ui_does_not_escape_the_service_listener(controller, monkeypatch) -> None:
    def broken_controls(*args):
        raise RuntimeError("widget unavailable")

    monkeypatch.setattr(controller.controls, "set_idle", broken_controls)

    controller.state_changed(ServiceState(ServiceMode.RECORDING, (), 0, "Recording"))

    assert controller.monitor.failure_count == 1


@pytest.mark.parametrize("diagnostics", [False, True])
def test_composition_can_omit_the_entire_monitoring_layer(diagnostics) -> None:
    from krita_brush_replay.composition import compose_runtime
    from krita_brush_replay.infrastructure.input_filter import CanvasInputFilter
    from krita_brush_replay.infrastructure.krita_replay import KritaReplayer
    from krita_brush_replay.observability.controls import DiagnosticControls
    from krita_brush_replay.ui.dock import BrushReplayPanel

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = BrushReplayPanel(object())

    content, runtime = compose_runtime(panel, object(), diagnostics=diagnostics)

    if diagnostics:
        assert content.findChild(DiagnosticControls) is not None
        assert type(runtime._input_filter) is not CanvasInputFilter
        assert type(runtime._host._replayer) is not KritaReplayer
    else:
        assert content is panel
        assert type(runtime._input_filter) is CanvasInputFilter
        assert type(runtime._host._replayer) is KritaReplayer
    runtime.close()
    content.deleteLater()
    application.processEvents()
