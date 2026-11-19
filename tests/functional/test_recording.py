"""Recording input, stopping, and retaining undo history."""

from dataclasses import replace

import pytest

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import BrushSample, ContextRef, InputPhase
from tests.support.recording import RECORDED, ReplayHost, draw


def test_stop_retains_the_captured_portion_and_later_input_is_ignored() -> None:
    """Stop retains the captured portion and later input is ignored."""

    host = ReplayHost()
    service = BrushReplayService(host)
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
def test_replay_contains_exactly_the_strokes_retained_after_history(actions, expected) -> None:
    """Replay contains exactly the strokes retained after history."""

    host = ReplayHost()
    service = BrushReplayService(host)
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


def test_new_recording_replaces_the_session_and_its_edits() -> None:
    """New recording replaces the session and its edits."""

    host = ReplayHost()
    service = BrushReplayService(host)
    service.start_recording()
    draw(service, 1)
    service.stop_recording()
    service.override_setting((0,), "size", 40.0)

    host.brush = replace(RECORDED, size=7.0)
    service.start_recording()
    draw(service, 2)
    service.stop_recording()
    service.replay()

    assert len(host.outputs[0].strokes) == 1
    assert host.outputs[0].strokes[0].samples[0].x == 2
    assert host.outputs[0].strokes[0].brush_state.size == 7.0


@pytest.mark.parametrize("trigger", ["release", "canvas-change", "capture-failure"])
def test_interruption_discards_only_unfinished_input_and_allows_a_new_recording(trigger) -> None:
    """Interruption discards only unfinished input and allows a new recording."""

    host = ReplayHost()
    service = BrushReplayService(host)
    service.start_recording()
    draw(service, 1)
    service.accept_input(InputPhase.PRESS, BrushSample(2, 4, 0.5, 20))
    if trigger == "capture-failure":
        service.capture_failed(RuntimeError("tablet disconnected"))
        assert "tablet disconnected" in service.state.message
    else:
        host.context = ContextRef("document", "another-layer")
        if trigger == "release":
            service.accept_input(InputPhase.RELEASE, None)
        else:
            service.canvas_changed()
        assert "changed" in service.state.message

    assert service.state.mode is ServiceMode.IDLE
    service.replay()
    assert [stroke.samples[0].x for stroke in host.outputs[0].strokes] == [1]
    service.start_recording()
    draw(service, 3)
    service.stop_recording()
    service.replay()
    assert [stroke.samples[0].x for stroke in host.outputs[-1].strokes] == [3]


@pytest.mark.qt
@pytest.mark.parametrize("mouse_release_first", [True, False])
def test_tablet_ownership_preserves_axes_without_duplicate_mouse_strokes(
    workbench, mouse_release_first
):
    """Tablet ownership preserves axes without duplicate mouse strokes."""

    from tests.support.krita import mouse, tablet

    bench = workbench
    canvas = bench.krita.canvas
    bench.click("Record new session")
    tablet(canvas, "Press", pressure=0.2)
    mouse(canvas, "ButtonPress")
    mouse(canvas, "Move")
    tablet(canvas, "Move", x=60, pressure=0.7)
    if mouse_release_first:
        mouse(canvas, "ButtonRelease")
    tablet(canvas, "Release", x=60)
    if not mouse_release_first:
        mouse(canvas, "ButtonRelease")
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
def test_resize_and_outside_input_are_excluded_without_losing_the_next_stroke(workbench, device):
    """Resize and outside input are excluded without losing the next stroke."""

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
def test_recording_requires_an_open_document_and_writable_layer(workbench):
    """Recording requires an open document and writable layer."""

    bench = workbench
    document = bench.krita.document
    bench.krita.document = None
    bench.click("Record new session")
    assert bench.message_contains("open a document")
    assert not bench.button("Replay").isEnabled()
    assert not bench.button("Stop").isEnabled()
    bench.krita.document = document
    bench.record()
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_lost_tablet_release_does_not_block_a_later_mouse_gesture(workbench):
    """Lost tablet release does not block a later mouse gesture."""

    from tests.support.krita import mouse, tablet

    bench = workbench
    bench.click("Record new session")
    tablet(bench.krita.canvas, "Press", pressure=0.4)
    tablet(bench.krita.canvas, "Move", pressure=0.6)
    mouse(bench.krita.canvas, "Move", held=False)
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
