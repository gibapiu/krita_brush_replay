"""Wire the Qt panel, event source, and host-independent service together."""

from __future__ import annotations

import logging

from krita_brush_replay.app.service import BrushReplayService, ServiceMode, ServiceState
from krita_brush_replay.infrastructure.diagnostics import configure_diagnostics
from krita_brush_replay.infrastructure.input_filter import CanvasInputFilter
from krita_brush_replay.infrastructure.krita_host import KritaHost
from krita_brush_replay.infrastructure.qt_facade import QtWidgets

logger = logging.getLogger(__name__)


class BrushReplayRuntime:
    def __init__(self, panel, *, krita_instance=None) -> None:
        from krita_brush_replay.infrastructure.report_export import ReportExporter

        self._panel = panel
        self._diagnostics = configure_diagnostics()
        self._report_exporter = ReportExporter(panel)
        self._report_exporter.finished.connect(panel.show_message)
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
        self._mode = ServiceMode.IDLE
        self._closed = False

        panel.record_requested.connect(self._service.start_recording)
        panel.stop_requested.connect(self._service.stop_recording)
        panel.replay_requested.connect(self._service.replay)
        panel.replay_current_requested.connect(self._service.replay_with_current_settings)
        panel.override_requested.connect(self._service.override_setting)
        panel.reset_requested.connect(self._service.reset_setting)
        panel.export_report_requested.connect(self.export_diagnostics)
        panel.destroyed.connect(self.close)
        self._service.subscribe(self._apply_state)

    def canvas_changed(self) -> None:
        self._host.invalidate_canvas_target()
        self._service.canvas_changed()

    def _apply_state(self, state: ServiceState) -> None:
        if self._closed:
            return
        self._panel.apply_state(state)
        if state.mode is not self._mode or state.mode is ServiceMode.IDLE:
            logger.info(
                "state mode=%s strokes=%s samples=%s message=%r",
                state.mode.name,
                state.stroke_count,
                state.sample_count,
                state.message,
            )
            if state.mode in (ServiceMode.RECORDING, ServiceMode.REPLAYING):
                logger.info("environment %s", self._host.diagnostic_context())
        self._set_filter_installed(state.mode is ServiceMode.RECORDING)
        recording_finished = self._mode is ServiceMode.RECORDING and state.can_replay
        self._mode = state.mode
        if recording_finished:
            self._load_brush_choices()

    def _load_brush_choices(self) -> None:
        try:
            choices = self._host.brush_choices()
        except Exception as error:
            self._panel.show_message(f"Could not list brush resources: {error}")
            return
        self._panel.set_choices(choices)

    def _set_filter_installed(self, installed: bool) -> None:
        if self._application is None or installed == self._filter_installed:
            return
        if installed:
            self._application.installEventFilter(self._input_filter)
        else:
            self._application.removeEventFilter(self._input_filter)
            self._input_filter.finish_capture()
        self._filter_installed = installed

    def export_diagnostics(self, path: str) -> None:
        try:
            state = self._service.state
            report = self._diagnostics.render(
                {
                    **self._host.diagnostic_context(),
                    "service_mode": state.mode.name,
                    "stroke_count": state.stroke_count,
                    "sample_count": state.sample_count,
                    "status": state.message,
                }
            )
            self._report_exporter.export(path, report)
        except Exception as error:
            logger.exception("diagnostic-export could not start")
            self._panel.show_message(f"Could not export diagnostic report: {error}")

    def close(self, *_args) -> None:
        if self._closed:
            return
        self._closed = True
        self._host.invalidate_canvas_target()
        self._service.unsubscribe(self._apply_state)
        self._set_filter_installed(False)
