"""Krita registration entry point for the Brush Replay docker."""

from __future__ import annotations

from krita_brush_replay.infrastructure.krita_facade import KritaFacade, load_krita

DOCK_FACTORY_ID = "krita_brush_replay_dock"
DOCK_TITLE = "Brush Replay"


def register_plugin(*, krita_instance=None, krita_api: KritaFacade | None = None) -> bool:
    if krita_api is None:
        krita_api = load_krita()
    if krita_api is None:
        return False
    application = krita_instance if krita_instance is not None else krita_api.application()

    dock_type = create_dock_widget_class(krita_api.dock_widget_base, application)
    krita_api.register_dock(application, DOCK_FACTORY_ID, dock_type)
    return True


def create_dock_widget_class(dock_widget_base: type, application) -> type:
    class BrushReplayDock(dock_widget_base):
        def __init__(self) -> None:
            super().__init__()
            from krita_brush_replay.composition import compose_runtime
            from krita_brush_replay.infrastructure.krita_brush_state import KritaColorCodec
            from krita_brush_replay.infrastructure.qt_facade import QtWidgets
            from krita_brush_replay.ui.dock import BrushReplayPanel

            self.setWindowTitle(DOCK_TITLE)
            self._panel = BrushReplayPanel(KritaColorCodec())
            content, self._runtime = compose_runtime(self._panel, application)
            scroll_area = QtWidgets.QScrollArea(self)
            scroll_area.setWidgetResizable(True)
            scroll_area.setWidget(content)
            self.setWidget(scroll_area)

        def canvasChanged(self, canvas) -> None:
            runtime = getattr(self, "_runtime", None)
            if runtime is not None:
                runtime.canvas_changed()

    BrushReplayDock.__name__ = "BrushReplayDock"
    return BrushReplayDock
