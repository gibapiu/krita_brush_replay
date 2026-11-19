from __future__ import annotations

import pytest


@pytest.fixture
def workbench(qapp, monkeypatch):
    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import Krita
    from tests.support.workbench import Workbench

    krita = Krita(monkeypatch)
    bench = Workbench(krita)
    qapp.processEvents()
    yield bench
    bench.runtime.close()
    bench.panel.close()
    bench.panel.deleteLater()
    krita.window.close()
    krita.window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, qt_enum(QtCore.QEvent, "Type", "DeferredDelete"))
    qapp.processEvents()


@pytest.fixture(scope="session")
def qapp(request):
    if request.config.getoption("--qt-api") == "none":
        raise pytest.UsageError("A real QApplication requires --qt-api=pyqt5 or pyqt6")
    from krita_brush_replay.infrastructure.qt_facade import QtWidgets

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()
