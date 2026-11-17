
from __future__ import annotations

from pathlib import Path

from krita_brush_replay.app.recording_library import LibraryState, RecordingInfo
from krita_brush_replay.app.service_state import ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import BrushChoices, StrokeSettings
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtWidgets, qt_enum
from krita_brush_replay.ui.stroke_editor import StrokeSettingsEditor

EDITED_STROKE_MARK = "✎"
_USER_ROLE = qt_enum(QtCore.Qt, "ItemDataRole", "UserRole")


class RecordingList(QtWidgets.QListWidget):
    def __init__(self, *, show_documents: bool, name: str) -> None:
        super().__init__()
        self.setObjectName(name)
        self._documents = show_documents
        self._shown = ()

    @property
    def key(self):
        item = self.currentItem()
        return None if item is None else item.data(_USER_ROLE)

    def show_recordings(self, rows: tuple[RecordingInfo, ...], selected=None) -> None:
        if rows == self._shown:
            return
        previous = self.key if selected is None else selected
        self._shown = rows
        blocked = self.blockSignals(True)
        scroll = self.verticalScrollBar().value()
        try:
            self.clear()
            for info in rows:
                item = QtWidgets.QListWidgetItem(_recording_label(info, self._documents))
                item.setData(_USER_ROLE, info.key)
                item.setToolTip(info.key.document or "Unsaved document")
                self.addItem(item)
                if info.key == previous:
                    self.setCurrentItem(item)
            self.verticalScrollBar().setValue(scroll)
        finally:
            self.blockSignals(blocked)

    def select_key(self, key) -> None:
        if self.key == key:
            return
        blocked = self.blockSignals(True)
        try:
            self.setCurrentRow(-1)
            for row in range(self.count()):
                if self.item(row).data(_USER_ROLE) == key:
                    self.setCurrentRow(row)
                    break
        finally:
            self.blockSignals(blocked)


class BrushReplayPanel(QtWidgets.QWidget):
    record_requested = QtCore.pyqtSignal()
    stop_requested = QtCore.pyqtSignal()
    replay_requested = QtCore.pyqtSignal()
    replay_current_requested = QtCore.pyqtSignal()
    override_requested = QtCore.pyqtSignal(object, str, object)
    reset_requested = QtCore.pyqtSignal(object, str)
    source_requested = QtCore.pyqtSignal(object)
    delete_requested = QtCore.pyqtSignal(object)
    refresh_requested = QtCore.pyqtSignal()

    def __init__(self, color_codec, parent=None) -> None:
        super().__init__(parent)
        self._state: ServiceState | None = None
        self._library = LibraryState()
        self._strokes: tuple[StrokeSettings, ...] = ()
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setObjectName("workflow")
        self._recordings = RecordingList(show_documents=False, name="recordings")
        self._library_list = RecordingList(show_documents=True, name="library")
        self._record_button = QtWidgets.QPushButton("New recording")
        self._stop_button = QtWidgets.QPushButton("Stop")
        self._record_delete = QtWidgets.QPushButton("Delete")
        self._record_delete.setObjectName("delete_recording")
        self._document_label = QtWidgets.QLabel("No document")
        self._record_count = QtWidgets.QLabel("0 strokes")
        self._search = QtWidgets.QLineEdit()
        self._search.setObjectName("library_search")
        self._search.setPlaceholderText("Search documents or dates")
        self._search.setClearButtonEnabled(True)
        self._refresh_button = QtWidgets.QPushButton("Refresh")
        self._library_delete = QtWidgets.QPushButton("Delete")
        self._library_delete.setObjectName("delete_library_recording")
        self._replay_button = QtWidgets.QPushButton("Replay")
        self._replay_button.setObjectName("replay")
        self._replay_mode = QtWidgets.QComboBox()
        self._replay_mode.setObjectName("replay_mode")
        self._replay_mode.addItems(("Recorded settings", "Current settings"))
        self._edit_button = QtWidgets.QToolButton()
        self._edit_button.setObjectName("edit_strokes")
        self._edit_button.setText("Edit strokes")
        self._edit_button.setCheckable(True)
        self._edit_area = QtWidgets.QWidget()
        self._edit_area.setObjectName("stroke_edits")
        self._stroke_list = QtWidgets.QListWidget()
        self._stroke_list.setObjectName("strokes")
        self._stroke_list.setSelectionMode(
            qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "ExtendedSelection")
        )
        self._select_all = QtWidgets.QPushButton("Select all strokes")
        self._settings_editor = StrokeSettingsEditor(color_codec)
        self._count_label = QtWidgets.QLabel("")
        self._status_label = QtWidgets.QLabel("Choose a recording.")
        self._status_label.setWordWrap(True)

        record_page = QtWidgets.QWidget()
        record_layout = QtWidgets.QVBoxLayout(record_page)
        record_layout.addWidget(self._document_label)
        record_layout.addLayout(_row(self._record_button, self._stop_button))
        record_layout.addWidget(self._record_count)
        record_layout.addWidget(self._recordings, 1)
        record_layout.addLayout(_row(self._record_delete))

        replay_page = QtWidgets.QWidget()
        replay_layout = QtWidgets.QVBoxLayout(replay_page)
        replay_layout.addWidget(self._search)
        replay_layout.addWidget(self._library_list, 1)
        replay_layout.addLayout(_row(self._refresh_button, self._library_delete))
        replay_layout.addLayout(_row(self._replay_mode, self._replay_button))
        replay_layout.addWidget(self._count_label)
        replay_layout.addWidget(self._edit_button)
        replay_layout.addWidget(self._edit_area)
        edit_layout = QtWidgets.QVBoxLayout(self._edit_area)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addWidget(self._stroke_list)
        edit_layout.addWidget(self._select_all)
        edit_layout.addWidget(self._settings_editor)
        self._edit_area.hide()

        self._tabs.addTab(record_page, "Record")
        self._tabs.addTab(replay_page, "Replay")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(self._status_label)

        self._record_button.clicked.connect(self.record_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        self._record_delete.clicked.connect(lambda: self._delete(self._recordings))
        self._library_delete.clicked.connect(lambda: self._delete(self._library_list))
        self._refresh_button.clicked.connect(self.refresh_requested)
        self._search.textChanged.connect(self._show_recordings)
        self._tabs.currentChanged.connect(self._tab_changed)
        self._recordings.currentItemChanged.connect(self._update_controls)
        self._recordings.itemDoubleClicked.connect(self._open_for_replay)
        self._library_list.currentItemChanged.connect(self._choose_recording)
        self._library_list.itemClicked.connect(self._choose_recording)
        self._library_list.itemActivated.connect(self._choose_recording)
        self._replay_button.clicked.connect(self._replay)
        self._edit_button.toggled.connect(self._edit_area.setVisible)
        self._select_all.clicked.connect(self._stroke_list.selectAll)
        self._stroke_list.itemSelectionChanged.connect(self._show_selected_settings)
        self._settings_editor.override_requested.connect(
            lambda setting, value: self.override_requested.emit(
                self._selected_indexes(), setting, value
            )
        )
        self._settings_editor.reset_requested.connect(
            lambda setting: self.reset_requested.emit(self._selected_indexes(), setting)
        )
        self._update_controls()

    def set_choices(self, choices: BrushChoices) -> None:
        self._settings_editor.set_choices(choices)
        self._show_selected_settings()

    def show_message(self, message: str) -> None:
        self._status_label.setText(message)

    def apply_library_state(self, state: LibraryState) -> None:
        self._library = state
        self._show_recordings()

    def apply_state(self, state: ServiceState) -> None:
        previous = self._state
        self._state = state
        self._document_label.setText(
            Path(state.document).name if state.document else (
                "Unsaved document" if state.document_open else "No document"
            )
        )
        self._document_label.setToolTip(state.document or "")
        self._record_count.setText(f"{state.recorded_strokes} strokes · {state.recorded_samples} samples")
        self._record_button.setEnabled(state.can_record)
        self._stop_button.setEnabled(state.can_stop)
        old, self._strokes = self._strokes, state.strokes if state.source is not None else ()
        self._show_strokes(old)
        if previous is None or (previous.current_recording, previous.document) != (
            state.current_recording, state.document
        ):
            self._show_recordings()
        if previous is None or previous.source != state.source:
            self._library_list.select_key(state.source)
        self._show_selected_settings()
        self._update_controls()

    def _show_recordings(self, *_args) -> None:
        state = self._state
        current = None if state is None else state.current_recording
        rows = tuple(row for row in self._library.recordings if row.key != current)
        if current is not None:
            rows = (RecordingInfo(current, None), *rows)
        document = None if state is None else state.document
        self._recordings.show_recordings(tuple(row for row in rows if row.key.document == document))
        query = self._search.text().casefold().strip()
        shown = tuple(row for row in rows if query in (
            (row.key.document or "Unsaved document") + " " + _recording_label(row, True)
        ).casefold())
        self._library_list.show_recordings(shown, None if state is None else state.source)
        self._update_controls()

    def _update_controls(self, *_args) -> None:
        state = self._state
        idle = state is not None and state.mode is ServiceMode.IDLE
        selected = state is not None and state.source is not None and self._library_list.key == state.source
        self._recordings.setEnabled(idle)
        self._library_list.setEnabled(idle)
        self._record_delete.setEnabled(idle and self._recordings.key is not None and not self._library.busy)
        self._library_delete.setEnabled(idle and self._library_list.key is not None and not self._library.busy)
        self._refresh_button.setEnabled(not self._library.busy)
        self._replay_button.setEnabled(bool(selected and state.can_replay))
        self._replay_mode.setEnabled(bool(selected and idle))
        self._edit_button.setEnabled(bool(selected and state.can_replay))
        if not selected:
            self._edit_button.setChecked(False)
        self._count_label.setText(
            f"{state.stroke_count} strokes · {state.sample_count} samples" if selected else "Choose a recording."
        )
        if state is not None:
            message = "" if state.can_stop else self._library.message
            self._status_label.setText(message or state.message)

    def _choose_recording(self, *_args) -> None:
        key = self._library_list.key
        if key is not None:
            self.source_requested.emit(key)
        self._update_controls()

    def _open_for_replay(self, item) -> None:
        self._tabs.setCurrentIndex(1)
        key = item.data(_USER_ROLE)
        self._library_list.select_key(key)
        self.source_requested.emit(key)

    def _tab_changed(self, *_args) -> None:
        if self._tabs.currentIndex() == 0:
            self._edit_button.setChecked(False)
        self.refresh_requested.emit()
        self._update_controls()

    def _replay(self) -> None:
        signal = self.replay_current_requested if self._replay_mode.currentIndex() else self.replay_requested
        signal.emit()

    def _delete(self, recordings: RecordingList) -> None:
        key = recordings.key
        if key is None:
            return
        yes = qt_enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
        no = qt_enum(QtWidgets.QMessageBox, "StandardButton", "No")
        answer = QtWidgets.QMessageBox.question(self, "Delete recording", "Delete this recording?", yes | no, no)
        if answer == yes:
            self.delete_requested.emit((key,))

    def _show_strokes(self, previous: tuple[StrokeSettings, ...]) -> None:
        while self._stroke_list.count() > len(self._strokes):
            self._stroke_list.takeItem(self._stroke_list.count() - 1)
        shown = self._stroke_list.count()
        for index, stroke in enumerate(self._strokes):
            if index >= shown:
                self._stroke_list.addItem(_stroke_label(index, stroke))
            elif index >= len(previous) or previous[index] is not stroke:
                self._stroke_list.item(index).setText(_stroke_label(index, stroke))

    def _show_selected_settings(self) -> None:
        selected = [self._strokes[index] for index in self._selected_indexes()]
        editable = self._state is not None and self._state.can_replay
        self._settings_editor.setEnabled(editable and bool(selected))
        self._settings_editor.show_settings(selected)
        self._select_all.setEnabled(editable)

    def _selected_indexes(self) -> tuple[int, ...]:
        rows = (self._stroke_list.row(item) for item in self._stroke_list.selectedItems())
        return tuple(sorted(row for row in rows if row < len(self._strokes)))


def _row(*widgets):
    layout = QtWidgets.QHBoxLayout()
    for widget in widgets:
        layout.addWidget(widget)
    return layout


def _recording_label(info: RecordingInfo, document: bool) -> str:
    stamp = "Current" if info.created is None else info.created.astimezone().strftime("%d %b %H:%M:%S")
    source = Path(info.key.document).name if info.key.document else "Unsaved document"
    return f"{source} · {stamp}" if document else stamp


def _stroke_label(index: int, stroke: StrokeSettings) -> str:
    brush = stroke.effective
    mark = f" {EDITED_STROKE_MARK}" if stroke.override.overridden_settings else ""
    return f"{index + 1}. {brush.preset.name} · {round(brush.size, 2):g} px{mark}"
