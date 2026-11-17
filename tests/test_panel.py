from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from krita_brush_replay.app.service_state import ServiceMode, ServiceState
from krita_brush_replay.app.recording_library import LibraryState, RecordingInfo, RecordingKey
from krita_brush_replay.domain.brush_override import BrushChoices, StrokeSettings
from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtGui, QtWidgets

pytestmark = pytest.mark.skipif(
    not hasattr(QtWidgets, "QListWidget"),
    reason="PyQt6 is not installed",
)

INK = ResourceRef("preset", "Ink", "ink.kpp")
PENCIL = ResourceRef("preset", "Pencil", "pencil.kpp")
RECORDED = BrushState(
    preset=INK,
    foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
    background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
    size=10.0,
    opacity=1.0,
    flow=1.0,
    rotation=0.0,
    pattern=ResourceRef("pattern", "Paper", "paper.pat"),
    gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
    blending_mode="normal",
)


class ColorCodec:
    def to_qcolor(self, color: ColorState):
        return QtGui.QColor.fromRgbF(*color.components[:3])

    def from_qcolor(self, qcolor) -> ColorState:
        components = (qcolor.redF(), qcolor.greenF(), qcolor.blueF(), 1.0)
        return ColorState("RGBA", "U8", "sRGB", components)


@pytest.fixture
def panel():
    from krita_brush_replay.ui.dock import BrushReplayPanel

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = BrushReplayPanel(ColorCodec())
    widget.set_choices(BrushChoices(presets=(INK, PENCIL), blending_modes={"normal": "Normal"}))
    widget.findChild(QtWidgets.QTabWidget, "workflow").setCurrentIndex(1)
    widget.findChild(QtWidgets.QToolButton, "edit_strokes").click()
    yield widget
    widget.deleteLater()
    application.processEvents()


def idle(*strokes: StrokeSettings) -> ServiceState:
    key = RecordingKey("/art/cat.kra", "recording")
    return ServiceState(ServiceMode.IDLE, strokes, 0, "", source=key, current_recording=key)


def editor_field(panel, label: str) -> QtWidgets.QWidget:
    form = panel.findChild(QtWidgets.QGroupBox).layout()
    for row in range(form.rowCount()):
        if form.itemAt(row, QtWidgets.QFormLayout.ItemRole.LabelRole).widget().text() == label:
            field_row = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.FieldRole).layout()
            return field_row.itemAt(0).widget()
    raise LookupError(label)


def select_strokes(panel, *rows: int) -> None:
    stroke_list = panel.findChild(QtWidgets.QListWidget, "strokes")
    for row in rows:
        stroke_list.item(row).setSelected(True)


def test_edits_apply_to_the_selected_strokes(panel) -> None:
    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(idle(*(StrokeSettings(RECORDED) for _ in range(3))))
    select_strokes(panel, 0, 2)

    editor_field(panel, "Size").setValue(25.0)
    editor_field(panel, "Preset").activated.emit(1)

    assert requests == [((0, 2), "size", 25.0), ((0, 2), "preset", PENCIL)]


def test_typing_into_a_mixed_field_applies_the_typed_value(panel) -> None:
    from PyQt6.QtTest import QTest

    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(idle(StrokeSettings(RECORDED), StrokeSettings(replace(RECORDED, size=12.0))))
    select_strokes(panel, 0, 1)
    size = editor_field(panel, "Size")
    assert size.text() == "—"
    panel.findChild(QtWidgets.QToolButton, "edit_strokes").setChecked(True)
    size.setFocus()

    QTest.keyClicks(size, "25")
    QTest.keyClick(size, QtCore.Qt.Key.Key_Return)

    assert requests == [((0, 1), "size", 25.0)]


def test_record_scope_library_choice_search_and_confirmed_delete(panel, monkeypatch):
    from PyQt6.QtTest import QTest

    current = RecordingKey("/art/cat.kra", "current")
    older = RecordingKey("/art/cat.kra", "older")
    foreign = RecordingKey("/closed/dog.kra", "dog")
    now = datetime.now(timezone.utc)
    state = ServiceState(
        ServiceMode.IDLE, (), 0, "Choose a recording.",
        current_recording=current, document=current.document, document_open=True,
    )
    panel.apply_state(state)
    panel.apply_library_state(LibraryState((
        RecordingInfo(current, now), RecordingInfo(older, now - timedelta(minutes=2)),
        RecordingInfo(foreign, now - timedelta(days=1)),
    )))
    local = panel.findChild(QtWidgets.QListWidget, "recordings")
    library = panel.findChild(QtWidgets.QListWidget, "library")
    replay = panel.findChild(QtWidgets.QPushButton, "replay")
    assert local.count() == 2
    assert library.count() == 3
    assert not replay.isEnabled()
    assert panel.findChild(QtWidgets.QWidget, "stroke_edits").isHidden()
    requests, deleted, painted = [], [], []
    panel.source_requested.connect(requests.append)
    panel.delete_requested.connect(deleted.append)
    panel.replay_requested.connect(lambda: painted.append("recorded"))
    panel.replay_current_requested.connect(lambda: painted.append("current"))
    panel.findChild(QtWidgets.QLineEdit, "library_search").setText("dog")
    assert library.count() == 1
    assert local.count() == 2
    library.setCurrentRow(0)
    assert requests == [foreign]
    panel.show()
    QtWidgets.QApplication.processEvents()
    QTest.mouseClick(
        library.viewport(), QtCore.Qt.MouseButton.LeftButton,
        pos=library.visualItemRect(library.item(0)).center(),
    )
    assert requests == [foreign, foreign]
    panel.apply_state(replace(state, source=foreign, strokes=(StrokeSettings(RECORDED),)))
    assert replay.isEnabled()
    replay.click()
    panel.findChild(QtWidgets.QComboBox, "replay_mode").setCurrentIndex(1)
    replay.click()
    assert painted == ["recorded", "current"]
    remove = panel.findChild(QtWidgets.QPushButton, "delete_library_recording")
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.StandardButton.No
    )
    remove.click()
    assert deleted == []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.StandardButton.Yes
    )
    remove.click()
    assert deleted == [(foreign,)]
