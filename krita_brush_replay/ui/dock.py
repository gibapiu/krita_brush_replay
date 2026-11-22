"""Small signal-driven Brush Replay docker panel."""

from __future__ import annotations

from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import BrushChoices, RecordingSettings
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets
from krita_brush_replay.ui.stroke_editor import StrokeSettingsEditor


class BrushReplayPanel(QtWidgets.QWidget):
    record_requested = QtCore.pyqtSignal()
    stop_requested = QtCore.pyqtSignal()
    replay_requested = QtCore.pyqtSignal()
    replay_current_requested = QtCore.pyqtSignal()
    override_requested = QtCore.pyqtSignal(str, object)
    reset_requested = QtCore.pyqtSignal(str)

    def __init__(self, color_codec, parent=None) -> None:
        super().__init__(parent)
        self._shown_settings: RecordingSettings | None = None
        self._record_button = QtWidgets.QPushButton("Record new session")
        self._stop_button = QtWidgets.QPushButton("Stop")
        self._settings_editor = StrokeSettingsEditor(color_codec)
        self._settings_editor.setEnabled(False)
        self._replay_button = QtWidgets.QPushButton("Replay")
        self._replay_current_button = QtWidgets.QPushButton("Compare with current settings")
        self._count_label = QtWidgets.QLabel("0 strokes · 0 samples")
        self._status_label = QtWidgets.QLabel("")

        self._record_button.setCheckable(True)

        self._record_button.setToolTip("Replace the current recording and capture new strokes")
        self._stop_button.setToolTip("Finish recording")
        self._replay_button.setToolTip(
            "Paint on the active layer with each stroke's recorded and changed settings"
        )
        self._replay_current_button.setToolTip(
            "Paint on the active layer using the brush settings currently selected"
        )
        self._status_label.setWordWrap(True)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self._record_button)
        button_row.addWidget(self._stop_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self._settings_editor)
        layout.addWidget(self._replay_button)
        layout.addWidget(self._replay_current_button)
        layout.addWidget(self._count_label)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._record_button.clicked.connect(self.record_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        self._replay_button.clicked.connect(self.replay_requested)
        self._replay_current_button.clicked.connect(self.replay_current_requested)
        self._settings_editor.override_requested.connect(self.override_requested)
        self._settings_editor.reset_requested.connect(self.reset_requested)

    def set_choices(self, choices: BrushChoices) -> None:
        self._settings_editor.set_choices(choices)
        if self._shown_settings is not None:
            self._settings_editor.show_settings(self._shown_settings)

    def show_message(self, message: str) -> None:
        self._status_label.setText(message)

    def apply_state(self, state: ServiceState, settings: RecordingSettings) -> None:
        self._record_button.setEnabled(state.can_record)
        self._stop_button.setEnabled(state.can_stop)
        self._replay_button.setEnabled(state.can_replay)
        self._replay_current_button.setEnabled(state.can_replay)
        self._count_label.setText(f"{state.stroke_count} strokes · {state.sample_count} samples")
        self._status_label.setText(state.message)
        self._record_button.setChecked(state.mode is ServiceMode.RECORDING)
        self._settings_editor.setEnabled(state.can_edit)
        if settings != self._shown_settings:
            self._settings_editor.show_settings(settings)
            self._shown_settings = settings
