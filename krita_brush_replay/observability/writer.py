from __future__ import annotations

from pathlib import Path
from threading import Thread

from krita_brush_replay.infrastructure.qt_facade import QtCore


class ReportExporter(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)

    def export(self, path: str, report: str) -> None:
        Thread(target=self._write, args=(path, report), daemon=True).start()

    def _write(self, path: str, report: str) -> None:
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as error:
            message = f"Could not export diagnostic report: {error}"
        else:
            message = "Diagnostic report exported. Attach it to your bug report."
        self._notify_if_open(message)

    def _notify_if_open(self, message: str) -> None:
        try:
            self.finished.emit(message)
        except RuntimeError:
            return
