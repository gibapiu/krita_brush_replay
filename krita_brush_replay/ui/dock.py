"""Small signal-driven Brush Replay docker panel."""

from __future__ import annotations

from collections.abc import Sequence

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
        self._strokes: Sequence[StrokeSettings] = ()
        self._can_edit_strokes = False
        self._selected_rows: tuple[int, ...] = ()
        self._settings_revision = -1
        self._summary_key = None
        self._record_button = QtWidgets.QPushButton("Record new session")
        self._stop_button = QtWidgets.QPushButton("Stop")
        self._stroke_list = QtWidgets.QListView()
        self._stroke_model = StrokeListModel(self._stroke_list)
        self._stroke_list.setModel(self._stroke_model)
        self._stroke_list.setUniformItemSizes(True)
        self._stroke_list.setLayoutMode(qt_enum(QtWidgets.QListView, "LayoutMode", "Batched"))
        self._select_all_button = QtWidgets.QPushButton("Select all strokes")
        self._settings_editor = StrokeSettingsEditor(color_codec)
        self._settings_editor.setEnabled(False)
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
        self._stroke_list.selectionModel().selectionChanged.connect(self._selection_changed)
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
        self._summary_key = None
        self._show_selected_settings()

    def show_message(self, message: str) -> None:
        self._status_label.setText(message)

    def apply_state(self, state: ServiceState, strokes: Sequence[StrokeSettings]) -> None:
        self._record_button.setEnabled(state.can_record)
        self._stop_button.setEnabled(state.can_stop)
        self._replay_button.setEnabled(state.can_replay)
        self._replay_current_button.setEnabled(state.can_replay)
        self._select_all_button.setEnabled(state.can_replay)
        self._count_label.setText(f"{state.stroke_count} strokes · {state.sample_count} samples")
        self._status_label.setText(state.message)
        self._record_button.setChecked(state.mode is ServiceMode.RECORDING)
        self._strokes = strokes
        self._settings_revision = state.settings_revision
        was_editable = self._can_edit_strokes
        self._can_edit_strokes = state.can_replay
        self._stroke_model.apply(state, strokes)
        self._stroke_list.setEnabled(state.can_replay)
        if was_editable or self._can_edit_strokes:
            self._show_selected_settings()

    def _selection_changed(self, *_args) -> None:
        self._selected_rows = self._selected_indexes()
        self._show_selected_settings()

    def _show_selected_settings(self) -> None:
        enabled = self._can_edit_strokes and bool(self._selected_rows)
        self._settings_editor.setEnabled(enabled)
        if not self._can_edit_strokes and self._selected_rows:
            return
        key = (self._settings_revision, self._selected_rows)
        if key == self._summary_key:
            return
        selected = (self._strokes[row] for row in self._selected_rows)
        self._settings_editor.show_settings(selected)
        self._summary_key = key

    def _selected_indexes(self) -> tuple[int, ...]:
        rows = (index.row() for index in self._stroke_list.selectionModel().selectedRows())
        return tuple(sorted(row for row in rows if row < len(self._strokes)))


def _stroke_label(index: int, stroke: StrokeSettings) -> str:
    brush = stroke.effective
    mark = f" {EDITED_STROKE_MARK}" if stroke.override.overridden_settings else ""
    return f"{index + 1}. {brush.preset.name} · {round(brush.size, 2):g} px{mark}"


class StrokeListModel(QtCore.QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._strokes: Sequence[StrokeSettings] = ()
        self._count = 0
        self._revision = -1

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def data(self, index, role=qt_enum(QtCore.Qt, "ItemDataRole", "DisplayRole")):
        if role != qt_enum(QtCore.Qt, "ItemDataRole", "DisplayRole") or not index.isValid():
            return None
        row = index.row()
        if row not in range(self._count):
            return None
        try:
            return _stroke_label(row, self._strokes[row])
        except (OSError, ValueError):
            return f"{row + 1}. Unavailable"

    def apply(self, state: ServiceState, strokes: Sequence[StrokeSettings]) -> None:
        previous = self._count
        count = len(strokes)
        if count > previous:
            self.beginInsertRows(QtCore.QModelIndex(), previous, count - 1)
            self._strokes = strokes
            self._count = count
            self.endInsertRows()
        elif count < previous:
            self.beginRemoveRows(QtCore.QModelIndex(), count, previous - 1)
            self._strokes = strokes
            self._count = count
            self.endRemoveRows()
        else:
            self._strokes = strokes
        if count and self._revision != state.settings_revision:
            self.dataChanged.emit(self.index(0), self.index(count - 1))
        self._revision = state.settings_revision
