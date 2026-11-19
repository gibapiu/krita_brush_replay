"""Opt-in panel update latency with a fixed recording workload."""

from time import perf_counter

import pytest

from krita_brush_replay.app.service import ServiceMode
from tests.support.recording import RECORDED


@pytest.mark.qt
def test_recording_growth_keeps_the_panel_within_its_update_budget(qapp, monkeypatch):
    """Two thousand stroke updates stay responsive and display the final stroke count."""

    from krita_brush_replay.app.service import ServiceState
    from krita_brush_replay.domain.brush_override import StrokeSettings
    from krita_brush_replay.infrastructure.krita_brush_state import KritaColorCodec
    from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets, qt_enum
    from krita_brush_replay.ui.dock import BrushReplayPanel
    from tests.support.krita import Krita

    krita = Krita(monkeypatch)
    panel = BrushReplayPanel(KritaColorCodec())
    strokes = tuple(StrokeSettings(RECORDED) for _ in range(2000))
    states = [
        ServiceState(ServiceMode.RECORDING, strokes[:count], count, "Recording")
        for count in range(1, 2001)
    ]
    try:
        started = perf_counter()
        for state in states:
            panel.apply_state(state)
        elapsed = perf_counter() - started
        assert elapsed < 2.0
        assert panel.findChild(QtWidgets.QListWidget).count() == 2000
        assert any("2000 strokes" in label.text() for label in panel.findChildren(QtWidgets.QLabel))
    finally:
        panel.deleteLater()
        krita.window.close()
        krita.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            None, qt_enum(QtCore.QEvent, "Type", "DeferredDelete")
        )
        qapp.processEvents()
