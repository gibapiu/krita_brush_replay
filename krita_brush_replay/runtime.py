
from __future__ import annotations

from collections.abc import Callable

from krita_brush_replay.app.service_state import ServiceMode, ServiceState
from krita_brush_replay.infrastructure.host.input_filter import CanvasInputFilter
from krita_brush_replay.infrastructure.host.krita_host import KritaHost
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtWidgets, qt_enum

SAVE_ON_CLOSE_TIMEOUT_S = 5.0
UNSAVED_ON_CLOSE_WARNING = (
    "Brush Replay could not save all recordings before closing: a write failed or "
    f"saving did not finish within {SAVE_ON_CLOSE_TIMEOUT_S:g} seconds. "
    "Unsaved strokes may be missing when you reopen the documents."
)


class UiThreadCalls(QtCore.QObject):

    requested = QtCore.pyqtSignal(object)

    def __init__(self, parent=None, on_error: Callable[[Exception], None] = lambda error: None):
        super().__init__(parent)
        self._on_error = on_error
        self.requested.connect(self._run, qt_enum(QtCore.Qt, "ConnectionType", "QueuedConnection"))

    def _run(self, callback) -> None:
        try:
            callback()
        except Exception as error:
            try:
                self._on_error(error)
            except Exception:
                pass


class BrushReplayRuntime:
    def __init__(
        self,
        panel,
        *,
        service_factory,
        krita_instance=None,
        host_factory=KritaHost,
        input_filter_factory=CanvasInputFilter,
        callback_error: Callable[[Exception], None] = lambda error: None,
    ) -> None:
        self._panel = panel
        self._host = host_factory(krita_instance)
        self._ui_thread_calls = UiThreadCalls(panel, callback_error)
        self._service = service_factory(
            self._host, self._ui_thread_calls.requested.emit, self._apply_state,
            self._apply_library_state,
        )
        self._application = QtWidgets.QApplication.instance()
        self._input_filter = input_filter_factory(
            self._host,
            self._service.accept_input,
            self._service.capture_failed,
            panel,
        )
        self._capturing = False
        self._stop_watching_history = None
        self._stop_watching_saves = self._host.watch_saves(self._document_saved)
        self._mode = ServiceMode.IDLE
        self._had_strokes = False
        self._source = None
        self._disposed = False

        panel.record_requested.connect(self._service.start_recording)
        panel.stop_requested.connect(self._service.stop_recording)
        panel.replay_requested.connect(self._service.replay)
        panel.replay_current_requested.connect(self._service.replay_with_current_settings)
        panel.override_requested.connect(self._service.override_setting)
        panel.reset_requested.connect(self._service.reset_setting)
        panel.source_requested.connect(self._service.select_recording)
        panel.delete_requested.connect(self._service.delete_recordings)
        panel.refresh_requested.connect(self._service.library.refresh)
        panel.destroyed.connect(self.close)
        self._apply_state(self._service.state)
        self._apply_library_state(self._service.library.state)
        self._service.library.refresh()

    def canvas_changed(self) -> None:
        self._service.canvas_changed()
        self._service.library.refresh()

    def _document_saved(self) -> None:
        self._service.document_saved()
        self._service.library.refresh()

    def _apply_library_state(self, state) -> None:
        if not self._disposed:
            self._panel.apply_library_state(state)

    def _apply_state(self, state: ServiceState) -> None:
        if self._disposed:
            return
        self._panel.apply_state(state)
        self._set_capturing(state.mode is ServiceMode.RECORDING)
        finished_recording = self._mode is ServiceMode.RECORDING and state.mode is ServiceMode.IDLE
        recording_loaded = state.stroke_count > 0 and (
            finished_recording or not self._had_strokes or state.source != self._source
        )
        self._mode = state.mode
        self._had_strokes = state.stroke_count > 0
        self._source = state.source
        if recording_loaded:
            self._load_brush_choices()
        if finished_recording:
            self._service.library.refresh()

    def _load_brush_choices(self) -> None:
        try:
            choices = self._host.brush_choices()
        except Exception as error:
            self._panel.show_message(f"Could not list brush resources: {error}")
            return
        self._panel.set_choices(choices)

    def _set_capturing(self, capturing: bool) -> None:
        if capturing == self._capturing:
            return
        if capturing:
            self._stop_watching_history = self._host.watch_history(
                self._service.undo_triggered,
                self._service.redo_triggered,
            )
            if self._application is not None:
                self._application.installEventFilter(self._input_filter)
        else:
            self._stop_watching_history()
            if self._application is not None:
                self._application.removeEventFilter(self._input_filter)
            self._input_filter.reset()
        self._capturing = capturing

    def close(self, *_args) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._stop_watching_saves()
        self._set_capturing(False)
        if not self._service.close(SAVE_ON_CLOSE_TIMEOUT_S):
            QtWidgets.QMessageBox.warning(None, "Brush Replay", UNSAVED_ON_CLOSE_WARNING)
