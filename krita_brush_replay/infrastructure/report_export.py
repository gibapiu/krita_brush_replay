"""Write a diagnostic snapshot without blocking Krita's UI thread."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread

from krita_brush_replay.infrastructure.qt_facade import QtCore

logger = logging.getLogger(__name__)


class ReportExporter(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)

    def export(self, path: str, report: str) -> None:
        Thread(target=self._write, args=(path, report), daemon=True).start()

    def _write(self, path: str, report: str) -> None:
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as error:
            logger.exception("diagnostic-export failed")
            message = f"Could not export diagnostic report: {error}"
        else:
            logger.info("diagnostic-export complete")
            message = "Diagnostic report exported. Attach it to your bug report."
        try:
            self.finished.emit(message)
        except RuntimeError:
            # The docker may have been closed while the file was being written.
            logger.debug("diagnostic-export receiver was closed")
