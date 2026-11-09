"""Small signal-driven Brush Replay docker panel."""

from __future__ import annotations

from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets


class BrushReplayPanel(QtWidgets.QWidget):
    record_requested = QtCore.pyqtSignal()
    stop_requested = QtCore.pyqtSignal()
    replay_requested = QtCore.pyqtSignal()
    replay_current_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._record_button = QtWidgets.QPushButton("Record new session")
        self._stop_button = QtWidgets.QPushButton("Stop")
        self._replay_button = QtWidgets.QPushButton("Replay original settings")
        self._replay_current_button = QtWidgets.QPushButton("Compare with current settings")
        self._count_label = QtWidgets.QLabel("0 strokes · 0 samples")
        self._status_label = QtWidgets.QLabel("")

        self._record_button.setCheckable(True)

        self._record_button.setToolTip("Replace the current recording and capture new strokes")
        self._stop_button.setToolTip("Finish recording")
        self._replay_button.setToolTip(
            "Paint each stroke with the brush settings captured when it began"
        )
        self._replay_current_button.setToolTip(
            "Paint all recorded geometry using the brush settings currently selected"
        )
        self._status_label.setWordWrap(True)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self._record_button)
        button_row.addWidget(self._stop_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self._replay_button)
        layout.addWidget(self._replay_current_button)
        layout.addWidget(self._count_label)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._record_button.clicked.connect(self.record_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        self._replay_button.clicked.connect(self.replay_requested)
        self._replay_current_button.clicked.connect(self.replay_current_requested)

    def apply_state(self, state: ServiceState) -> None:
        self._record_button.setEnabled(state.can_record)
        self._stop_button.setEnabled(state.can_stop)
        self._replay_button.setEnabled(state.can_replay)
        self._replay_current_button.setEnabled(state.can_replay)
        self._count_label.setText(
            f"{state.stroke_count} strokes · {state.sample_count} samples"
        )
        self._status_label.setText(state.message)
        active = state.mode is ServiceMode.RECORDING
        self._record_button.setChecked(active)
