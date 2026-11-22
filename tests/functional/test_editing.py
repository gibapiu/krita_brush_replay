"""Editing selected strokes and restoring their recorded settings."""

from time import monotonic

import pytest

from tests.support.recording import RECORDED, ReplayHost, draw


def test_returning_to_displayed_precision_restores_exact_recorded_value(service_factory) -> None:
    """Returning to displayed precision restores exact recorded value."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    draw(service, 1)
    service.stop_recording()
    service.override_setting((0,), "size", 40.0)
    service.override_setting((0,), "opacity", 0.25)

    service.override_setting((0,), "size", 12.35)
    service.replay()

    assert host.outputs[0].strokes[0].brush_state.size == 12.345
    assert host.outputs[0].strokes[0].brush_state.opacity == 0.25
    service.reset_setting((0,), "opacity")
    service.replay()
    assert host.outputs[-1].strokes[0].brush_state == RECORDED


@pytest.mark.qt
def test_bulk_selection_reset_and_preset_edits_affect_only_selected_output(workbench):
    """Bulk selection reset and preset edits affect only selected output."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.record(3)
    bench.select(0, 2)
    bench.number("Size", 40)
    bench.number("Opacity", 25)
    bench.number("Flow", 30)
    bench.number("Rotation", 45)
    bench.choose("Preset", "Pencil")
    bench.choose("Pattern", "Canvas")
    bench.choose("Gradient", "Mesh")
    bench.choose("Blending mode", "Normal")
    bench.select(2)
    QtTest.QTest.mouseClick(
        bench.field("Reset size to the recorded value"),
        qt_enum(QtCore.Qt, "MouseButton", "LeftButton"),
    )
    bench.replay()

    settings = [state for state, _ in bench.krita.document.layer.writes]
    assert [
        (state["preset"], state["size"], state["opacity"], state["flow"], state["rotation"])
        for state in settings
    ] == [
        ("pencil.kpp", 40.0, 0.25, 0.3, 45.0),
        ("ink.kpp", 12.345, 0.8, 0.6, 32.0),
        ("pencil.kpp", 12.345, 0.25, 0.3, 45.0),
    ]
    assert [
        (settings[index]["pattern"], settings[index]["gradient"], settings[index]["blending_mode"])
        for index in (0, 2)
    ] == [
        ("canvas.pat", "mesh.svg", "normal"),
        ("canvas.pat", "mesh.svg", "normal"),
    ]


@pytest.mark.qt
def test_fixed_size_preset_uses_the_size_accepted_by_its_engine(workbench):
    """Fixed size preset uses the size accepted by its engine."""

    bench = workbench
    bench.record()
    bench.select(0)
    bench.choose("Preset", "Fixed")
    bench.replay()
    assert bench.krita.document.layer.writes[0][0]["size"] == 1.0
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_mixed_selection_changes_only_after_a_deliberate_edit(workbench):
    """Mixed selection changes only after a deliberate edit."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.click("Record new session")
    bench.stroke()
    bench.krita.view.setBrushSize(25)
    bench.krita.view.setCurrentBrushPreset(bench.krita.pencil)
    bench.stroke(90)
    bench.click("Stop")
    bench.click("Select all strokes")
    size = bench.field("Size")
    preset = bench.field("Preset")
    assert size.text() == "—"
    size.setFocus()
    QtTest.QTest.keyClick(size, qt_enum(QtCore.Qt, "Key", "Key_Up"))
    preset.setFocus()
    QtTest.QTest.keyClick(preset, qt_enum(QtCore.Qt, "Key", "Key_Down"))
    assert size.text() == "—"
    assert not bench.field("Reset size to the recorded value").isEnabled()

    size.setFocus()
    QtTest.QTest.keyClicks(size, "40")
    QtTest.QTest.keyClick(size, qt_enum(QtCore.Qt, "Key", "Key_Return"))
    assert bench.field("Reset size to the recorded value").isEnabled()
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes] == [40, 40]


@pytest.mark.qt
def test_edited_marker_disappears_only_after_every_override_is_cleared(workbench):
    """Edited marker disappears only after every override is cleared."""

    bench = workbench
    bench.record()
    bench.select(0)
    bench.number("Size", 40)
    bench.number("Opacity", 25)
    bench.number("Size", 12.35)
    assert not bench.field("Reset size to the recorded value").isEnabled()
    assert "✎" in bench.stroke_labels()[0]
    bench.number("Opacity", 80)
    assert not bench.field("Reset opacity to the recorded value").isEnabled()
    assert "✎" not in bench.stroke_labels()[0]


@pytest.mark.qt
def test_unfocused_scrolling_and_minimum_stepping_do_not_create_edits(workbench):
    """Unfocused scrolling and minimum stepping do not create edits."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.krita.view.opacity = 0
    bench.record()
    bench.select(0)
    opacity = bench.field("Opacity")
    opacity.setFocus()
    QtTest.QTest.keyClick(opacity, qt_enum(QtCore.Qt, "Key", "Key_Down"))
    assert opacity.value() == 0
    bench.button("Replay").setFocus()
    assert not opacity.hasFocus()
    for title in ("Opacity", "Preset"):
        field = bench.field(title)
        wheel = QtGui.QWheelEvent(
            QtCore.QPointF(5, 5),
            QtCore.QPointF(field.mapToGlobal(QtCore.QPoint(5, 5))),
            QtCore.QPoint(0, 0),
            QtCore.QPoint(0, 120),
            qt_enum(QtCore.Qt, "MouseButton", "NoButton"),
            qt_enum(QtCore.Qt, "KeyboardModifier", "NoModifier"),
            qt_enum(QtCore.Qt, "ScrollPhase", "NoScrollPhase"),
            False,
        )
        QtWidgets.QApplication.sendEvent(field, wheel)
    assert not bench.field("Reset opacity to the recorded value").isEnabled()
    assert not bench.field("Reset preset to the recorded value").isEnabled()


@pytest.mark.qt
@pytest.mark.parametrize("action", ["cancel", "accept-unchanged", "pick"])
def test_colour_dialog_only_changes_recording_when_a_colour_is_chosen(workbench, action):
    """Colour dialog only changes recording when a colour is chosen."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.record()
    bench.select(0)
    handled = []
    timer = QtCore.QTimer()
    deadline = monotonic() + 2

    def handle_dialog():
        dialog = QtWidgets.QApplication.activeModalWidget()
        if isinstance(dialog, QtWidgets.QColorDialog):
            timer.stop()
            handled.append(True)
            if action == "pick":
                dialog.setCurrentColor(QtGui.QColor.fromRgbF(1, 0, 0, 1))
            dialog.reject() if action == "cancel" else dialog.accept()
        elif monotonic() >= deadline:
            timer.stop()
            if dialog is not None:
                dialog.reject()

    timer.timeout.connect(handle_dialog)
    timer.start(1)
    try:
        QtTest.QTest.mouseClick(
            bench.field("Foreground"), qt_enum(QtCore.Qt, "MouseButton", "LeftButton")
        )
    finally:
        timer.stop()
    assert handled, "The intended colour dialog did not appear"
    bench.replay()
    actual = bench.krita.document.layer.writes[0][0]["foreground"]
    assert actual == ((1, 0, 0, 1) if action == "pick" else (0.1, 0.2, 0.3, 1))


@pytest.mark.qt
def test_edits_and_replay_do_not_need_a_writable_recording_file(workbench, recording_io):
    """Disk-full during idle editing leaves capture data and replay available."""

    bench = workbench
    bench.record(3)
    recording_io.error = "No space left on device"
    bench.click("Select all strokes")
    bench.number("Size", 40)
    assert all("Unavailable" not in label for label in bench.stroke_labels())
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes] == [40, 40, 40]
    recording_io.error = None
    bench.number("Size", 50)
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes[-3:]] == [50, 50, 50]


@pytest.mark.qt
def test_retry_after_a_failed_reset_clears_the_previous_selection_and_edits(
    workbench, recording_io
):
    """A replacement recording shows its own strokes and settings after a storage failure."""

    bench = workbench
    bench.record()
    bench.select(0)
    bench.number("Size", 40)
    recording_io.error = "new recording unavailable"
    bench.click("Record new session")
    assert bench.message_contains("new recording unavailable")
    assert bench.button("Record new session").isEnabled()
    assert not bench.button("Replay").isEnabled()
    assert bench.stroke_labels() == ()
    recording_io.error = None
    bench.record()
    assert not bench.field("Size").isEnabled()
    bench.select(0)
    assert bench.field("Size").value() == 12.35
    assert not bench.field("Reset size to the recorded value").isEnabled()
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes] == [12.345]
