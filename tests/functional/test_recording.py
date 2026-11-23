"""Recording input, stopping, and retaining undo history."""

from dataclasses import replace

import pytest

from krita_brush_replay.app.service import ServiceMode
from krita_brush_replay.domain.recording import BrushSample, ContextRef, InputPhase
from tests.support.recording import (
    RECORDED,
    ReplayHost,
    draw,
    draw_samples,
    record_when_available,
    varied_samples,
)


def test_stop_retains_the_captured_portion_and_later_input_is_ignored(service_factory) -> None:
    """Stop retains the captured portion and later input is ignored."""

    host = ReplayHost()
    service = service_factory(host)
    points = (BrushSample(1.25, 2.5, 0.2, 100), BrushSample(3.75, 4.5, 0.7, 125))
    service.start_recording()
    service.accept_input(InputPhase.PRESS, points[0])
    service.accept_input(InputPhase.MOVE, points[1])

    service.stop_recording()
    service.accept_input(InputPhase.MOVE, BrushSample(9, 9, 1, 140))
    service.accept_input(InputPhase.RELEASE, None)
    service.replay()

    assert tuple(host.outputs[0].strokes[0].samples) == points
    assert service.state.mode is ServiceMode.IDLE
    assert (service.state.stroke_count, service.state.sample_count) == (1, 2)


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        ((1, 2, 3, "undo", "undo", "redo"), [1, 2]),
        ((1, "undo", "undo", "redo"), []),
        ((1, "undo", "undo", "redo", "redo"), [1]),
        ((1, 2, "undo", 3, "redo"), [1, 3]),
        (("undo", "redo", "redo", 1, "redo"), [1]),
    ],
    ids=("retained-order", "pre-recording-undo", "matching-redos", "new-branch", "empty-history"),
)
def test_replay_contains_exactly_the_strokes_retained_after_history(
    actions, expected, service_factory
) -> None:
    """Replay contains exactly the strokes retained after history."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    for action in actions:
        if action == "undo":
            service.undo_triggered()
        elif action == "redo":
            service.redo_triggered()
        else:
            draw(service, action)
    service.stop_recording()
    service.replay()

    output = host.outputs[0].strokes if expected else ()
    assert [stroke.samples[0].x for stroke in output] == expected
    assert (service.state.stroke_count, service.state.sample_count) == (
        len(expected),
        len(expected),
    )
    if not expected:
        assert host.outputs == []
        assert not service.state.can_replay


def test_only_a_valid_new_recording_replaces_the_session_and_edits(service_factory):
    """An invalid Record keeps the session and edits; a valid one replaces them."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    draw(service, 1)
    service.stop_recording()
    service.override_setting("size", 40.0)

    host.context = None
    service.start_recording()
    host.context = ContextRef("document", "recorded-layer")
    service.replay()
    assert host.outputs[0].strokes[0].samples[0].x == 1
    assert host.outputs[0].strokes[0].brush_state.size == 40

    host.brush = replace(RECORDED, size=7.0)
    service.start_recording()
    draw(service, 2)
    service.stop_recording()
    service.replay()
    assert len(host.outputs[-1].strokes) == 1
    assert host.outputs[-1].strokes[0].samples[0].x == 2
    assert host.outputs[-1].strokes[0].brush_state.size == 7.0


@pytest.mark.qt
@pytest.mark.parametrize("echo", ["before-pen", "during-pen", "after-pen"])
def test_a_pen_drag_records_once_with_its_axes_when_krita_drops_the_mouse_echo(workbench, echo):
    """A pen drag records once with its axes while Krita drops the system mouse echo."""

    from tests.support.krita import QtCore, QtWidgets, mouse, qt_enum, tablet

    bench = workbench
    canvas = bench.krita.canvas

    def mouse_echo():
        mouse(canvas, "ButtonPress", krita_drops=True)
        mouse(canvas, "Move", x=60)
        mouse(canvas, "ButtonRelease", x=60)

    bench.click("Record new session")
    if echo == "before-pen":
        mouse_echo()
    tablet(canvas, "Press", pressure=0.2)
    tablet(canvas, "Press", pressure=0.25)
    mouse(canvas, "Move", held=False)
    if echo == "during-pen":
        mouse_echo()
    QtWidgets.QApplication.sendEvent(canvas, QtCore.QEvent(qt_enum(QtCore.QEvent, "Type", "Leave")))
    tablet(canvas, "Move", x=60, pressure=0.7)
    tablet(canvas, "Release", x=60)
    if echo == "after-pen":
        mouse_echo()
    bench.stroke(90)
    bench.click("Stop")
    bench.replay()

    writes = bench.krita.document.layer.writes
    assert len(writes) == 2
    assert [(p.pressure, p.x_tilt, p.y_tilt, p.rotation) for p in writes[0][1]] == [
        (0.2, -8, 14, 35.0),
        (0.7, -8, 14, 35.0),
    ]
    assert [p.tangential_pressure for p in writes[0][1]] == pytest.approx([0.6, 0.6], abs=1e-7)
    assert [point.pressure for point in writes[1][1]] == [1.0, 1.0]


@pytest.mark.qt
@pytest.mark.parametrize("device", ["mouse", "tablet"])
def test_unpainted_and_outside_input_is_excluded_without_losing_the_next_stroke(workbench, device):
    """Resize, popup-closing and outside input are excluded without losing the next stroke."""

    from tests.support.krita import mouse, tablet

    bench = workbench
    bench.click("Record new session")
    send = mouse if device == "mouse" else tablet
    phases = (
        ("ButtonPress", "Move", "ButtonRelease")
        if device == "mouse"
        else ("Press", "Move", "Release")
    )
    for phase in phases:
        send(bench.krita.canvas, phase, shift=True)
    bench.krita.canvas.popup_palette.show()
    for phase in phases:
        send(bench.krita.canvas, phase, x=50)
    mouse(bench.panel, "ButtonPress")
    mouse(bench.panel, "Move")
    mouse(bench.panel, "ButtonRelease")
    bench.stroke(80)
    bench.resize_brush_during_drag(25, x=160)
    bench.click("Stop")
    bench.replay()

    writes = bench.krita.document.layer.writes
    assert len(writes) == 1
    assert writes[0][1][0].position.x() == 80
    assert writes[0][0]["size"] == 12.345


@pytest.mark.qt
@pytest.mark.parametrize("unavailable", ["document", "paint-layer"])
def test_recording_requires_a_writable_canvas_and_accepts_unsaved_documents(workbench, unavailable):
    """Record rejects unavailable canvases and accepts an unsaved writable one."""

    bench = workbench
    document = bench.krita.document
    if unavailable == "document":
        bench.krita.document = None
    else:
        document.layer.writable = False
    bench.click("Record new session")
    assert bench.message_contains("open a document" if unavailable == "document" else "writable")
    assert not bench.button("Replay").isEnabled()
    assert not bench.button("Stop").isEnabled()
    bench.krita.document = document
    document.layer.writable = True
    assert document.fileName() == ""
    bench.record()
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
@pytest.mark.parametrize("pen_up", ["hover", "out-of-range", "left-canvas"])
def test_lost_tablet_release_does_not_block_a_later_mouse_gesture(workbench, pen_up):
    """A pen drag whose release is lost ends once the pen hovers, leaves range or the canvas."""

    from tests.support.krita import QtCore, QtWidgets, mouse, qt_enum, tablet

    bench = workbench
    canvas = bench.krita.canvas
    bench.click("Record new session")
    tablet(canvas, "Press", pressure=0.4)
    tablet(canvas, "Move", pressure=0.6)
    mouse(canvas, "Move", held=False)
    if pen_up == "hover":
        tablet(canvas, "Move", x=70, pressure=0, held=False)
    elif pen_up == "out-of-range":
        tablet(canvas, "LeaveProximity", pressure=0, held=False)
    else:
        leave = QtCore.QEvent(qt_enum(QtCore.QEvent, "Type", "Leave"))
        QtWidgets.QApplication.sendEvent(canvas, leave)
    bench.stroke(90)
    bench.click("Stop")
    bench.replay()
    assert [
        [point.pressure for point in points] for _, points in bench.krita.document.layer.writes
    ] == [[0.4, 0.6], [1, 1]]


@pytest.mark.qt
def test_stopping_a_tablet_gesture_does_not_leave_mouse_capture_locked_on_restart(workbench):
    """Starting again after an unfinished pen gesture still records ordinary mouse strokes."""

    from tests.support.krita import tablet

    bench = workbench
    bench.click("Record new session")
    tablet(bench.krita.canvas, "Press", pressure=0.4)
    bench.click("Stop")
    bench.record()
    bench.replay()
    assert [
        [point.pressure for point in points] for _, points in bench.krita.document.layer.writes
    ] == [[1, 1]]


@pytest.mark.qt
@pytest.mark.parametrize(
    "interruption", ["release", "canvas-change", "capture-failure", "undo", "resize"]
)
def test_interrupting_a_long_stroke_preserves_completed_input_and_allows_restart(
    interruption, workbench
):
    """Interruptions discard the active stroke while keeping completed input."""

    from tests.support.krita import Layer, mouse

    bench = workbench
    bench.click("Record new session")
    bench.stroke(30)
    mouse(bench.krita.canvas, "ButtonPress", x=90)
    for index in range(1000):
        mouse(bench.krita.canvas, "Move", x=120 + index / 13)

    if interruption == "capture-failure":
        bench.krita.capture_error = "tablet disconnected"
        mouse(bench.krita.canvas, "ButtonRelease", x=120)
        assert bench.message_contains("tablet disconnected")
        bench.krita.capture_error = None
    elif interruption == "undo":
        bench.krita.action("edit_undo").trigger()
    elif interruption == "resize":
        bench.krita.view.setBrushSize(40)
        mouse(bench.krita.canvas, "ButtonRelease", x=120)
    else:
        bench.krita.document.layer = Layer(bench.krita, "another-layer")
        if interruption == "release":
            mouse(bench.krita.canvas, "ButtonRelease", x=120)
        else:
            bench.runtime.canvas_changed()
        assert bench.message_contains("changed")
    if bench.button("Stop").isEnabled():
        bench.click("Stop")
    bench.replay()
    assert len(bench.krita.document.layer.writes) == 1
    assert bench.krita.document.layer.writes[0][1][0].position.x() == 30
    bench.record()
    bench.replay()
    assert len(bench.krita.document.layer.writes) == 2


def test_editing_all_strokes_preserves_each_stroke_s_other_settings(service_factory):
    """Editing one setting for all strokes keeps every other recorded setting per stroke."""

    host = ReplayHost()
    service = service_factory(host)
    brushes = [replace(RECORDED, size=float(index % 96 + 1)) for index in range(288)]
    service.start_recording()
    for index, brush in enumerate(brushes):
        host.brush = brush
        draw(service, index)
    service.stop_recording()
    service.override_setting("opacity", 0.25)
    service.replay()
    strokes = host.outputs[-1].strokes
    assert [stroke.brush_state for stroke in strokes] == [
        replace(brush, opacity=0.25) for brush in brushes
    ]
    assert [stroke.samples[0].x for stroke in strokes] == list(range(len(brushes)))
    service.reset_setting("opacity")
    service.replay()
    assert [stroke.brush_state for stroke in host.outputs[-1].strokes] == brushes


@pytest.mark.parametrize("failure", ["disk-full", "timeout"])
def test_storage_failure_keeps_complete_written_strokes_and_their_edits(
    failure, service_factory, recording_io
):
    """A failed write keeps the recorded prefix, excludes undone input and allows retry."""

    from time import monotonic

    host = ReplayHost()
    service = service_factory(host)
    recording_io.remaining = 180_000
    recording_io.stall_when_full = failure == "timeout"
    expected = []
    service.start_recording()
    draw(service, -1)
    service.undo_triggered()
    for stroke_index in range(20):
        points = tuple(varied_samples(500, seed=stroke_index))
        draw_samples(service, points)
        expected.append(points)
    started = monotonic()
    service.stop_recording()
    assert monotonic() - started < 3
    kept = service.state.stroke_count
    assert 0 < kept < len(expected)
    assert f"Kept {kept}" in service.state.message
    if failure == "disk-full":
        assert "not enough disk space" in service.state.message
        assert "Free some space" in service.state.message
    else:
        assert "storage is not responding" in service.state.message
        service.start_recording()
        assert "Try again shortly" in service.state.message
        assert service.state.stroke_count == kept
    assert service.state.sample_count == sum(len(points) for points in expected[:kept])
    service.override_setting("size", 40.0)
    service.replay()
    assert [tuple(stroke.samples) for stroke in host.outputs[-1].strokes] == expected[:kept]
    assert all(stroke.brush_state.size == 40 for stroke in host.outputs[-1].strokes)
    recording_io.release.set()
    recording_io.remaining = None
    record_when_available(service)
    draw(service, 99)
    service.stop_recording()
    service.replay()
    assert [stroke.samples[0].x for stroke in host.outputs[-1].strokes] == [99]


def test_stalled_storage_stops_capture_without_waiting_for_disk(service_factory, recording_io):
    """Capture reports backpressure without waiting for a blocked write."""

    from time import monotonic

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    recording_io.blocked.set()
    started = monotonic()
    try:
        service.accept_input(InputPhase.PRESS, BrushSample(0, 4, 0.5, 0))
        for point in varied_samples(100_000):
            service.accept_input(InputPhase.MOVE, point)
            if service.state.mode is ServiceMode.IDLE:
                break
        assert recording_io.entered.is_set()
        assert service.state.mode is ServiceMode.IDLE
        assert "cannot keep up" in service.state.message
        assert not service.state.can_replay
        assert monotonic() - started < 5
    finally:
        recording_io.release.set()
