"""Small signal-driven Brush Replay docker panel."""

from __future__ import annotations

from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import BrushChoices, StrokeSettings
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets, qt_enum
from krita_brush_replay.ui.stroke_editor import StrokeSettingsEditor

EDITED_STROKE_MARK = "✎"


class BrushReplayPanel(QtWidgets.QWidget):
    record_requested = QtCore.pyqtSignal()
    stop_requested = QtCore.pyqtSignal()
    replay_requested = QtCore.pyqtSignal()
    replay_current_requested = QtCore.pyqtSignal()
    override_requested = QtCore.pyqtSignal(object, str, object)
    reset_requested = QtCore.pyqtSignal(object, str)

    def __init__(self, color_codec, parent=None) -> None:
        super().__init__(parent)
        self._strokes: tuple[StrokeSettings, ...] = ()
        self._can_edit_strokes = False
        self._record_button = QtWidgets.QPushButton("Record new session")
        self._stop_button = QtWidgets.QPushButton("Stop")
        self._stroke_list = QtWidgets.QListWidget()
        self._select_all_button = QtWidgets.QPushButton("Select all strokes")
        self._settings_editor = StrokeSettingsEditor(color_codec)
        self._replay_button = QtWidgets.QPushButton("Replay")
        self._replay_current_button = QtWidgets.QPushButton("Compare with current settings")
        self._count_label = QtWidgets.QLabel("0 strokes · 0 samples")
        self._status_label = QtWidgets.QLabel("")

        self._record_button.setCheckable(True)
        self._stroke_list.setSelectionMode(
            qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "ExtendedSelection")
        )

        self._record_button.setToolTip("Replace the current recording and capture new strokes")
        self._stop_button.setToolTip("Finish recording")
        self._stroke_list.setToolTip(
            f"Select strokes to change their settings. {EDITED_STROKE_MARK} marks changed strokes."
        )
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
        layout.addWidget(self._stroke_list, 1)
        layout.addWidget(self._select_all_button)
        layout.addWidget(self._settings_editor)
        layout.addWidget(self._replay_button)
        layout.addWidget(self._replay_current_button)
        layout.addWidget(self._count_label)
        layout.addWidget(self._status_label)

        self._record_button.clicked.connect(self.record_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        self._replay_button.clicked.connect(self.replay_requested)
        self._replay_current_button.clicked.connect(self.replay_current_requested)
        self._select_all_button.clicked.connect(self._stroke_list.selectAll)
        self._stroke_list.itemSelectionChanged.connect(self._show_selected_settings)
        self._settings_editor.override_requested.connect(
            lambda setting, value: self.override_requested.emit(
                self._selected_indexes(), setting, value
            )
        )
        self._settings_editor.reset_requested.connect(
            lambda setting: self.reset_requested.emit(self._selected_indexes(), setting)
        )

    def set_choices(self, choices: BrushChoices) -> None:
        self._settings_editor.set_choices(choices)
        self._show_selected_settings()

    def show_message(self, message: str) -> None:
        self._status_label.setText(message)

    def apply_state(self, state: ServiceState) -> None:
        self._record_button.setEnabled(state.can_record)
        self._stop_button.setEnabled(state.can_stop)
        self._replay_button.setEnabled(state.can_replay)
        self._replay_current_button.setEnabled(state.can_replay)
        self._select_all_button.setEnabled(state.can_replay)
        self._count_label.setText(
            f"{state.stroke_count} strokes · {state.sample_count} samples"
        )
        self._status_label.setText(state.message)
        self._record_button.setChecked(state.mode is ServiceMode.RECORDING)
        previous_strokes, self._strokes = self._strokes, state.strokes
        self._can_edit_strokes = state.can_replay
        self._show_strokes(previous_strokes)
        self._show_selected_settings()

    def _show_strokes(self, previous: tuple[StrokeSettings, ...]) -> None:
        while self._stroke_list.count() > len(self._strokes):
            self._stroke_list.takeItem(self._stroke_list.count() - 1)
        for index, stroke in enumerate(self._strokes):
            item = self._stroke_list.item(index)
            if item is None:
                self._stroke_list.addItem(_stroke_label(index, stroke))
            elif index >= len(previous) or previous[index] is not stroke:
                item.setText(_stroke_label(index, stroke))

    def _show_selected_settings(self) -> None:
        selected = [self._strokes[index] for index in self._selected_indexes()]
        self._settings_editor.setEnabled(self._can_edit_strokes and bool(selected))
        self._settings_editor.show_settings(selected)

    def _selected_indexes(self) -> tuple[int, ...]:
        rows = (self._stroke_list.row(item) for item in self._stroke_list.selectedItems())
        return tuple(sorted(row for row in rows if row < len(self._strokes)))


def _stroke_label(index: int, stroke: StrokeSettings) -> str:
    brush = stroke.effective
    mark = f" {EDITED_STROKE_MARK}" if stroke.override.overridden_settings else ""
    return f"{index + 1}. {brush.preset.name} · {round(brush.size, 2):g} px{mark}"
