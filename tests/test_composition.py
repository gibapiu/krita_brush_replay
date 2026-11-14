import logging
from types import SimpleNamespace

from krita_brush_replay.composition import compose_runtime
from krita_brush_replay.infrastructure.qt_facade import QtWidgets
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.replay import ObservedReplayer


class Signal:
    def connect(self, callback):
        pass


def test_composition_wires_logging_without_changing_panel_or_host_logging() -> None:
    root_logger = logging.getLogger()
    plugin_logger = logging.getLogger("krita_brush_replay")
    before = [
        (logger.level, logger.propagate, logger.handlers[:])
        for logger in (root_logger, plugin_logger)
    ]
    qt_application = None
    if hasattr(QtWidgets, "QWidget"):
        qt_application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from krita_brush_replay.ui.dock import BrushReplayPanel

        panel = BrushReplayPanel(object())
    else:
        panel = SimpleNamespace(
            record_requested=Signal(),
            stop_requested=Signal(),
            replay_requested=Signal(),
            replay_current_requested=Signal(),
            override_requested=Signal(),
            reset_requested=Signal(),
            destroyed=Signal(),
            apply_state=lambda state: None,
        )
    application = object()

    runtime = compose_runtime(panel, application)

    try:
        assert runtime._panel is panel
        assert runtime._host._krita is application
        assert isinstance(runtime._input_filter, ObservedInputFilter)
        assert isinstance(runtime._host._replayer, ObservedReplayer)
        assert [
            (logger.level, logger.propagate, logger.handlers)
            for logger in (root_logger, plugin_logger)
        ] == before
    finally:
        runtime.close()
        if qt_application is not None:
            panel.deleteLater()
            qt_application.processEvents()
