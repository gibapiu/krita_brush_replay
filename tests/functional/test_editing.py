"""Editing all strokes and restoring their recorded settings."""

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
    service.override_setting("size", 40.0)
    service.override_setting("opacity", 0.25)

    service.override_setting("size", 12.35)
    service.replay()

    assert host.outputs[0].strokes[0].brush_state.size == 12.345
    assert host.outputs[0].strokes[0].brush_state.opacity == 0.25
    service.reset_setting("opacity")
    service.replay()
    assert host.outputs[-1].strokes[0].brush_state == RECORDED


@pytest.mark.qt
def test_edits_and_resets_apply_to_every_stroke(workbench):
    """Edits and resets apply to every stroke."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.record(3)
    bench.number("Size", 40)
    bench.number("Opacity", 25)
    bench.number("Flow", 30)
    bench.number("Rotation", 45)
    bench.choose("Preset", "Pencil")
    bench.choose("Pattern", "Canvas")
    bench.choose("Gradient", "Mesh")
    bench.choose("Blending mode", "Normal")
    QtTest.QTest.mouseClick(
        bench.field("Reset size to the recorded value"),
        qt_enum(QtCore.Qt, "MouseButton", "LeftButton"),
    )
    bench.replay()

    settings = [state for state, _ in bench.krita.document.layer.writes]
    assert [
        (state["preset"], state["size"], state["opacity"], state["flow"], state["rotation"])
        for state in settings
    ] == [("pencil.kpp", 12.345, 0.25, 0.3, 45.0)] * 3
    assert [
        (state["pattern"], state["gradient"], state["blending_mode"]) for state in settings
    ] == [("canvas.pat", "mesh.svg", "normal")] * 3


@pytest.mark.qt
def test_fixed_size_preset_uses_the_size_accepted_by_its_engine(workbench):
    """Fixed size preset uses the size accepted by its engine."""

    bench = workbench
    bench.record()
    bench.choose("Preset", "Fixed")
    bench.replay()
    assert bench.krita.document.layer.writes[0][0]["size"] == 1.0
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_mixed_recorded_values_change_only_after_a_deliberate_edit(workbench):
    """Mixed recorded values change only after a deliberate edit."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.click("Record new session")
    bench.stroke()
    bench.krita.view.setBrushSize(25)
    bench.krita.view.setCurrentBrushPreset(bench.krita.pencil)
    bench.stroke(90)
    bench.click("Stop")
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
def test_typing_the_recorded_value_clears_only_that_edit(workbench):
    """Typing the recorded value clears only that edit."""

    bench = workbench
    bench.record()
    bench.number("Size", 40)
    bench.number("Opacity", 25)
    bench.number("Size", 12.35)
    assert not bench.field("Reset size to the recorded value").isEnabled()
    assert bench.field("Reset opacity to the recorded value").isEnabled()
    bench.number("Opacity", 80)
    assert not bench.field("Reset opacity to the recorded value").isEnabled()


@pytest.mark.qt
def test_unfocused_scrolling_and_minimum_stepping_do_not_create_edits(workbench):
    """Unfocused scrolling and minimum stepping do not create edits."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets, qt_enum
    from tests.support.krita import QtTest

    bench = workbench
    bench.krita.view.opacity = 0
    bench.record()
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
    bench.number("Size", 40)
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes] == [40, 40, 40]
    recording_io.error = None
    bench.number("Size", 50)
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes[-3:]] == [50, 50, 50]


@pytest.mark.qt
def test_retry_after_a_failed_reset_clears_the_previous_edits(
    workbench, recording_io
):
    """A replacement recording shows its own strokes and settings after a storage failure."""

    bench = workbench
    bench.record()
    bench.number("Size", 40)
    recording_io.error = "new recording unavailable"
    bench.click("Record new session")
    assert bench.message_contains("new recording unavailable")
    assert bench.button("Record new session").isEnabled()
    assert not bench.button("Replay").isEnabled()
    assert not bench.field("Size").isEnabled()
    recording_io.error = None
    bench.record()
    assert bench.field("Size").value() == 12.35
    assert not bench.field("Reset size to the recorded value").isEnabled()
    bench.replay()
    assert [state["size"] for state, _ in bench.krita.document.layer.writes] == [12.345]


@pytest.mark.qt
def test_unreadable_brush_settings_disable_editing_until_they_can_be_read(
    workbench, recording_io
):
    """Unreadable brush settings disable editing until a later action reads them."""

    bench = workbench
    bench.click("Record new session")
    bench.stroke()
    recording_io.read_error = "Input/output error"
    bench.click("Stop")
    assert bench.message_contains("Could not read brush settings")
    assert not bench.field("Size").isEnabled()
    assert bench.button("Record new session").isEnabled()
    recording_io.read_error = None
    bench.replay()
    assert bench.field("Size").isEnabled()
    assert bench.field("Size").value() == 12.35
