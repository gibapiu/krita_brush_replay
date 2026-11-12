from __future__ import annotations

from datetime import datetime

from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets


class DiagnosticControls(QtWidgets.QGroupBox):
    output_changed = QtCore.pyqtSignal(bool)
    collection_changed = QtCore.pyqtSignal(bool)
    export_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__("Diagnostics", parent)
        self._output = QtWidgets.QCheckBox("Log to Krita output")
        self._collection = QtWidgets.QCheckBox("Collect diagnostic report")
        self._export = QtWidgets.QPushButton("Export diagnostic report…")
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._output.setChecked(True)
        self._collection.setChecked(True)
        layout = QtWidgets.QVBoxLayout(self)
        for widget in (self._output, self._collection, self._export, self._status):
            layout.addWidget(widget)
        self._output.toggled.connect(self.output_changed)
        self._collection.toggled.connect(self.collection_changed)
        self._export.clicked.connect(self._choose_path)

    def set_idle(self, idle: bool) -> None:
        for widget in (self._output, self._collection, self._export):
            widget.setEnabled(idle)

    def show_message(self, message: str) -> None:
        self._status.setText(message)

    def _choose_path(self) -> None:
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export diagnostic report",
            f"brush-replay-report-{datetime.now():%Y%m%d-%H%M%S}.txt",
            "Text reports (*.txt)",
        )
        if path:
            self.export_requested.emit(path)
