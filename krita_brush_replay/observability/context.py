from __future__ import annotations

from krita_brush_replay.infrastructure.qt_facade import QtCore

INPUT_SETTINGS = {
    "tabletPressureCurve": "0,0;1,1;",
    "LineSmoothingType": "unknown",
    "LineSmoothingSmoothPressure": "unknown",
    "LineSmoothingStabilizeSensors": "unknown",
}


class KritaDiagnosticContext:
    def __init__(self, application) -> None:
        self._application = application

    def snapshot(self) -> dict[str, object]:
        result: dict[str, object] = {
            "qt_version": getattr(QtCore, "QT_VERSION_STR", "unknown"),
            "pyqt_version": getattr(QtCore, "PYQT_VERSION_STR", "unknown"),
        }
        try:
            result["krita_version"] = self._application.version()
            result["input_settings"] = {
                key: self._application.readSetting("", key, default)
                for key, default in INPUT_SETTINGS.items()
            }
            action = self._application.action("disable_pressure")
            result["pressure_action_checked"] = action.isChecked() if action else None
        except Exception as error:
            result["context_error"] = str(error)
        return result

    def brush(self) -> dict[str, object]:
        view = self._application.activeWindow().activeView()
        preset = view.currentBrushPreset()
        return {
            "preset": preset.name(),
            "filename": preset.filename(),
            "size": view.brushSize(),
            "opacity": view.paintingOpacity(),
            "flow": view.paintingFlow(),
        }
