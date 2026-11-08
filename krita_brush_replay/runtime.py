"""Wire the Qt panel, event source, and host-independent service together."""

from __future__ import annotations

from krita_brush_replay.app.service import BrushReplayService, ServiceMode, ServiceState
from krita_brush_replay.infrastructure.input_filter import CanvasInputFilter
from krita_brush_replay.infrastructure.krita_host import KritaHost
from krita_brush_replay.infrastructure.qt_facade import QtWidgets


class BrushReplayRuntime:
    def __init__(self, panel, *, krita_instance=None) -> None:
        self._panel = panel
        self._host = KritaHost(krita_instance)
        self._service = BrushReplayService(self._host)
        self._application = QtWidgets.QApplication.instance()
        self._input_filter = CanvasInputFilter(
            self._host,
            self._service.accept_input,
            self._service.capture_failed,
            panel,
        )
        self._filter_installed = False
        self._closed = False

        panel.record_requested.connect(self._service.start_recording)
        panel.stop_requested.connect(self._service.stop_recording)
        panel.replay_requested.connect(self._service.replay)
        panel.destroyed.connect(self.close)
        self._service.subscribe(self._apply_state)

    def canvas_changed(self) -> None:
        self._service.canvas_changed()

    def _apply_state(self, state: ServiceState) -> None:
        if self._closed:
            return
        self._panel.apply_state(state)
        self._set_filter_installed(state.mode is ServiceMode.RECORDING)

    def _set_filter_installed(self, installed: bool) -> None:
        if self._application is None or installed == self._filter_installed:
            return
        if installed:
            self._application.installEventFilter(self._input_filter)
        else:
            self._application.removeEventFilter(self._input_filter)
        self._filter_installed = installed

    def close(self, *_args) -> None:
        if self._closed:
            return
        self._closed = True
        self._service.unsubscribe(self._apply_state)
        self._set_filter_installed(False)
