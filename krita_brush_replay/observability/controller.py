from __future__ import annotations

from krita_brush_replay.app.service import ServiceMode
from krita_brush_replay.infrastructure.krita_replay import KritaReplayer
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.context import KritaDiagnosticContext
from krita_brush_replay.observability.controls import DiagnosticControls
from krita_brush_replay.observability.monitor import Monitor
from krita_brush_replay.observability.replay import ObservedReplayer, ObservedSyntheticInput
from krita_brush_replay.observability.writer import ReportExporter


class DiagnosticController:
    def __init__(self, application, parent=None) -> None:
        self.controls = DiagnosticControls(parent)
        self.monitor = Monitor()
        self._context = KritaDiagnosticContext(application)
        self._writer = ReportExporter(self.controls)
        self._state = None
        self.monitor.set_output(True)
        self.monitor.set_collection(True)
        self.controls.output_changed.connect(self.monitor.set_output)
        self.controls.collection_changed.connect(self.monitor.set_collection)
        self.controls.export_requested.connect(self.export)
        self._writer.finished.connect(self.controls.show_message)

    def create_input_filter(self, *args, **kwargs):
        return ObservedInputFilter(
            *args, **kwargs, monitor=self.monitor, brush_snapshot=self._context.brush
        )

    def create_replayer(self, *args, **kwargs):
        synthetic_input = kwargs.get("synthetic_input")
        if synthetic_input is not None:
            kwargs["synthetic_input"] = ObservedSyntheticInput(synthetic_input, self.monitor)
        return ObservedReplayer(
            KritaReplayer(*args, **kwargs), self.monitor, brush_snapshot=self._context.brush
        )

    def state_changed(self, state) -> None:
        previous, self._state = self._state, state
        try:
            self.controls.set_idle(state.mode is ServiceMode.IDLE)
        except Exception:
            self.monitor.failure_count += 1
        if previous is None or previous.mode is not state.mode or state.mode is ServiceMode.IDLE:
            self.monitor.observe(self._record_state, state)

    def _record_state(self, state) -> None:
        self.monitor.log(
            "state mode=%s strokes=%s samples=%s message=%r",
            state.mode.name,
            state.stroke_count,
            state.sample_count,
            state.message,
        )
        if state.mode in (ServiceMode.RECORDING, ServiceMode.REPLAYING):
            self.monitor.log("environment %s", self._context.snapshot())

    def export(self, path: str) -> None:
        try:
            context = self._context.snapshot()
            context["monitoring"] = self.monitor.metrics()
            if self._state is not None:
                context.update(
                    {
                        "service_mode": self._state.mode.name,
                        "stroke_count": self._state.stroke_count,
                        "sample_count": self._state.sample_count,
                        "status": self._state.message,
                    }
                )
            self._writer.export(path, self.monitor.report.render(context))
        except Exception as error:
            self.controls.show_message(f"Could not export diagnostic report: {error}")
