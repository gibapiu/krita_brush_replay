import pytest

from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets

pytestmark = pytest.mark.skipif(
    not hasattr(QtCore, "pyqtSignal"), reason="A real Qt binding is not installed"
)


@pytest.mark.parametrize("unwritable", [False, True])
def test_export_reports_background_write_success_or_failure(tmp_path, unwritable) -> None:
    from PyQt6.QtTest import QSignalSpy

    from krita_brush_replay.observability.writer import ReportExporter

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    exporter = ReportExporter()
    completed = QSignalSpy(exporter.finished)
    path = tmp_path / "report.txt"
    if unwritable:
        path = tmp_path / "missing-directory" / "report.txt"

    exporter.export(str(path), "Brush Replay report — 鉛筆\n")
    if not completed:
        assert completed.wait(2_000)
    application.processEvents()

    if unwritable:
        assert "Could not export diagnostic report" in completed[0][0]
    else:
        assert path.read_text(encoding="utf-8") == "Brush Replay report — 鉛筆\n"
        assert "Diagnostic report exported" in completed[0][0]
