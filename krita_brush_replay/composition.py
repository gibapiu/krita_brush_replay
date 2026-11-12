from __future__ import annotations

from krita_brush_replay.infrastructure.krita_host import KritaHost
from krita_brush_replay.infrastructure.qt_facade import QtWidgets
from krita_brush_replay.runtime import BrushReplayRuntime


def compose_runtime(panel, application, *, diagnostics: bool = True):
    if not diagnostics:
        return panel, BrushReplayRuntime(panel, krita_instance=application)

    from krita_brush_replay.observability.controller import DiagnosticController

    controller = DiagnosticController(application)
    runtime = BrushReplayRuntime(
        panel,
        krita_instance=application,
        host_factory=lambda application: KritaHost(
            application, replayer_factory=controller.create_replayer
        ),
        input_filter_factory=controller.create_input_filter,
        state_listener=controller.state_changed,
    )
    content = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(panel, 1)
    layout.addWidget(controller.controls)
    return content, runtime
