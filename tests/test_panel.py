from dataclasses import replace
from time import perf_counter

import pytest

from krita_brush_replay.app.service import ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import BrushChoices, StrokeSettings
from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets

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
    yield widget
    widget.deleteLater()
    application.processEvents()


def idle(*strokes: StrokeSettings) -> ServiceState:
    return ServiceState(ServiceMode.IDLE, strokes, 0, "")


def editor_field(panel, label: str) -> QtWidgets.QWidget:
    form = panel.findChild(QtWidgets.QGroupBox).layout()
    for row in range(form.rowCount()):
        if form.itemAt(row, QtWidgets.QFormLayout.ItemRole.LabelRole).widget().text() == label:
            field_row = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.FieldRole).layout()
            return field_row.itemAt(0).widget()
    raise LookupError(label)


def key_press(key) -> QtGui.QKeyEvent:
    return QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, QtCore.Qt.KeyboardModifier.NoModifier)


def select_strokes(panel, *rows: int) -> None:
    stroke_list = panel.findChild(QtWidgets.QListWidget)
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


def test_differing_values_show_a_mixed_marker_that_stepping_cannot_apply(panel) -> None:
    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(
        idle(StrokeSettings(RECORDED), StrokeSettings(replace(RECORDED, size=12.0, preset=PENCIL)))
    )
    select_strokes(panel, 0, 1)
    size = editor_field(panel, "Size")
    preset = editor_field(panel, "Preset")

    size.stepBy(1)
    preset.setFocus()
    QtWidgets.QApplication.sendEvent(preset, key_press(QtCore.Qt.Key.Key_Down))

    assert size.text() == "—"
    assert preset.currentText() == ""
    assert editor_field(panel, "Opacity").text() != "—"
    assert requests == []


def test_typing_into_a_mixed_field_applies_the_typed_value(panel) -> None:
    from PyQt6.QtTest import QTest

    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(idle(StrokeSettings(RECORDED), StrokeSettings(replace(RECORDED, size=12.0))))
    select_strokes(panel, 0, 1)
    size = editor_field(panel, "Size")
    size.setFocus()

    QTest.keyClicks(size, "25")
    QTest.keyClick(size, QtCore.Qt.Key.Key_Return)

    assert requests == [((0, 1), "size", 25.0)]


def test_numbers_never_step_below_their_minimum(panel) -> None:
    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(idle(StrokeSettings(replace(RECORDED, opacity=0.0))))
    select_strokes(panel, 0)
    opacity = editor_field(panel, "Opacity")

    opacity.stepBy(-1)

    assert opacity.text() == "0.00 %"
    assert requests == []


def test_scrolling_over_an_unfocused_field_changes_nothing(panel) -> None:
    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    panel.apply_state(idle(StrokeSettings(RECORDED)))
    select_strokes(panel, 0)
    wheel = QtGui.QWheelEvent(
        QtCore.QPointF(5, 5),
        QtCore.QPointF(5, 5),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, 120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    for label in ("Preset", "Opacity"):
        QtWidgets.QApplication.sendEvent(editor_field(panel, label), wheel)

    assert requests == []


def test_recording_many_strokes_keeps_panel_updates_fast(panel) -> None:
    strokes: tuple[StrokeSettings, ...] = ()
    started = perf_counter()

    for _ in range(2_000):
        strokes = (*strokes, StrokeSettings(RECORDED))
        panel.apply_state(ServiceState(ServiceMode.RECORDING, strokes, 0, ""))

    assert perf_counter() - started < 2.0


def test_accepting_the_colour_dialog_without_picking_changes_nothing(panel) -> None:
    requests = []
    panel.override_requested.connect(lambda *request: requests.append(request))
    red = ColorState("RGBA", "U8", "sRGB", (1.0, 0.0, 0.0, 1.0))
    red_stroke = StrokeSettings(replace(RECORDED, foreground=red))
    panel.apply_state(idle(StrokeSettings(RECORDED), red_stroke))
    select_strokes(panel, 0, 1)
    QtCore.QTimer.singleShot(0, lambda: QtWidgets.QApplication.activeModalWidget().accept())

    editor_field(panel, "Foreground").click()

    assert requests == []
