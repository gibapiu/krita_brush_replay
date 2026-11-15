from types import SimpleNamespace

from krita_brush_replay.composition import compose_runtime
from krita_brush_replay.infrastructure.qt_facade import QtWidgets
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.replay import ObservedReplayer


class Signal:
    def connect(self, callback):
        pass


def test_composition_wires_logging_around_capture_and_replay() -> None:
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

    runtime = compose_runtime(panel, object())

    try:
        assert isinstance(runtime._input_filter, ObservedInputFilter)
        assert isinstance(runtime._host._replayer, ObservedReplayer)
    finally:
        runtime.close()
        if qt_application is not None:
            panel.deleteLater()
            qt_application.processEvents()
