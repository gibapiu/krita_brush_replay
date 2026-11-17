
import pytest
from support import Layer, MouseEvent


def test_capture_edit_reset_and_compare_preserve_original_brushes(plugin_factory):
    plugin = plugin_factory()
    view, presets = plugin.krita.view, plugin.krita.presets
    plugin.start()
    view.setCurrentBrushPreset(presets.ink)
    view.foreground.setComponents([0.1, 0.2, 0.3, 1])
    view.flow, view.rotation, view.blending_mode = 0.6, 32, "multiply"
    recorded = view.snapshot()
    plugin.stroke(1)
    view.setCurrentBrushPreset(presets.pencil)
    plugin.stroke(3)
    plugin.stop()

    pencil = next(ref for ref in plugin.panel.choices.presets if ref.name == "Pencil")
    plugin.override((0,), "preset", pencil)
    plugin.override((0, 1), "size", 40.0)
    plugin.override((0, 1), "opacity", 0.25)
    plugin.reset((0,), "size")
    view.setCurrentBrushPreset(presets.airbrush)
    before = view.snapshot()
    plugin.replay()

    first, second = plugin.painted
    assert first.settings == {**recorded, "preset": "Pencil", "opacity": 0.25}
    assert (second.settings["size"], second.settings["opacity"]) == (40, 0.25)
    assert view.snapshot() == before
    assert [p.size for p in (presets.ink, presets.pencil, presets.airbrush)] == [12, 3, 80]

    plugin.replay(current=True)
    assert [paint.settings for paint in plugin.painted] == [before, before]
    assert view.snapshot() == before
    plugin.reset((0,), "preset")
    plugin.reset((0, 1), "size")
    plugin.reset((0, 1), "opacity")
    plugin.replay()
    assert plugin.painted[0].settings == recorded
    assert plugin.state.can_replay


@pytest.mark.parametrize("current", [False, True])
def test_tablet_geometry_and_axes_use_current_pressure_calibration(plugin_factory, current):
    plugin = plugin_factory()
    plugin.start()
    plugin.input("TabletPress", 1, 100, tablet=True, pressure=0.25)
    plugin.input("TabletMove", 3, 125, tablet=True, pressure=0.75)
    plugin.input("TabletRelease", 3, 130, tablet=True)
    plugin.stop()
    plugin.krita.pressure_curve = "0,0;0.5,0.8;1,1;"
    plugin.replay(current=current)

    first, second = plugin.painted[0].points
    assert first.position == (11, 24)
    assert second.position == (13, 24)
    assert [first.pressure, second.pressure] == pytest.approx([0.4, 0.9])
    assert (
        first.x_tilt, first.y_tilt, first.rotation, first.tangential_pressure
    ) == (-8, 14, 35, 0.6)
    assert (first.time, second.time) == (0, 25)
    assert second.speed > 0

    plugin.krita.pen_pressure_enabled = False
    plugin.replay(current=current)
    assert [point.pressure for point in plugin.painted[0].points] == [1, 1]
    plugin.krita.pen_pressure_enabled = True
    plugin.krita.pressure_curve = "0,0;1,1;"
    plugin.replay(current=current)
    assert [point.pressure for point in plugin.painted[0].points] == [0.25, 0.75]


def test_capture_ignores_resize_and_duplicate_mouse_events_from_a_pen(plugin_factory):
    plugin = plugin_factory()
    plugin.start()
    plugin.qt.send(object(), MouseEvent("MouseButtonPress", 1))
    plugin.stroke(1, shift=True)
    plugin.input("TabletPress", 2, 10, tablet=True, pressure=0.2)
    plugin.input("MouseButtonPress", 2, 11)
    plugin.input("MouseMove", 3, 12)
    plugin.input("TabletMove", 4, 20, tablet=True, pressure=0.8)
    plugin.input("MouseButtonRelease", 4, 21)
    plugin.input("TabletRelease", 4, 30, tablet=True)
    plugin.stroke(5)
    plugin.stop()
    plugin.replay()

    assert plugin.state.stroke_count == 2
    assert [[p.pressure for p in paint.points] for paint in plugin.painted] == [[0.2, 0.8], [1, 1]]


def test_undo_redo_and_stopping_mid_stroke_keep_only_the_visible_drawing(plugin_factory):
    plugin = plugin_factory()
    plugin.start()
    for x in (1, 2, 3):
        plugin.stroke(x)
    undo = plugin.krita.action("edit_undo").triggered.emit
    redo = plugin.krita.action("edit_redo").triggered.emit
    undo()
    undo()
    redo()
    undo()
    plugin.stroke(4)
    redo()
    plugin.input("MouseButtonPress", 5, 100)
    plugin.input("MouseMove", 6, 110)
    plugin.stop()
    plugin.replay()

    assert [paint.points[0].position[0] for paint in plugin.painted] == [11, 14, 15]
    assert len(plugin.painted[-1].points) == 2
    plugin.start()
    plugin.stroke(9)
    plugin.stop()
    plugin.replay()
    assert [paint.points[0].position[0] for paint in plugin.painted] == [19]


def test_context_changes_stop_capture_and_replay_without_affecting_the_new_target(
    plugin_factory,
):
    plugin = plugin_factory(native=False)
    plugin.start()
    plugin.stroke(1)
    plugin.input("MouseButtonPress", 3)
    plugin.krita.document.layer = Layer(plugin.krita.view, "new-layer", native=False)
    plugin.runtime.canvas_changed()

    assert not plugin.state.can_stop
    assert plugin.state.stroke_count == 1
    plugin.replay()
    assert [paint.points[0][0] for paint in plugin.painted] == [(1, 4)]

    canvas = plugin.krita.document.canvas
    before = plugin.krita.view.snapshot()
    plugin.replay(wait=False)
    while not canvas.pressed:
        plugin.loop.step()
    plugin.select(plugin.krita.add_document("/art/other.kra"))
    plugin.loop.run()
    assert not canvas.pressed
    assert plugin.painted == []
    assert plugin.krita.view.snapshot() == before


@pytest.mark.parametrize("failure", ["missing preset", "painting failed"])
def test_replay_failure_reports_the_problem_and_restores_the_brush(plugin_factory, failure):
    plugin = plugin_factory()
    plugin.start()
    plugin.krita.view.setCurrentBrushPreset(plugin.krita.presets.ink)
    plugin.stroke()
    plugin.stop()
    plugin.krita.view.setCurrentBrushPreset(plugin.krita.presets.airbrush)
    before = plugin.krita.view.snapshot()
    if failure == "missing preset":
        plugin.krita.resources = lambda kind: {}
    else:
        plugin.krita.document.layer.failure = failure
    plugin.replay()

    assert plugin.painted == []
    assert "failed" in plugin.state.message.lower()
    assert plugin.krita.view.snapshot() == before
    assert plugin.state.can_replay


@pytest.mark.parametrize("current", [False, True])
def test_legacy_replay_delivers_complete_gestures_and_preserves_the_current_brush(
    plugin_factory, current,
):
    plugin = plugin_factory(native=False)
    plugin.start()
    plugin.krita.view.setCurrentBrushPreset(plugin.krita.presets.ink)
    plugin.stroke(tablet=True)
    plugin.input("TabletPress", 5, 50, tablet=True)
    plugin.input("TabletRelease", 5, 55, tablet=True)
    plugin.stop()
    plugin.krita.view.setCurrentBrushPreset(plugin.krita.presets.airbrush)
    canvas = plugin.krita.document.canvas
    canvas.first_pen_preset = plugin.krita.presets.pencil
    before = plugin.krita.view.snapshot()
    plugin.replay(current=current)

    assert [len(paint.points) for paint in plugin.painted] == [2, 1]
    sizes = [paint.settings["size"] for paint in plugin.painted]
    assert sizes == ([80, 80] if current else [12, 12])
    assert plugin.painted[0].points[0][0] == (1, 4)
    assert plugin.painted[0].points[0][1].pressure == 0.5
    assert plugin.painted[0].points[1][2] - plugin.painted[0].points[0][2] == 25
    assert canvas.focused and not canvas.pressed
    assert canvas.events[-1] == "leave"
    assert plugin.krita.view.snapshot() == before
    assert plugin.state.can_replay


def test_unsaved_recording_stops_at_its_memory_limit_and_remains_replayable(plugin_factory):
    plugin = plugin_factory(document=None)
    plugin.start()
    plugin.input("MouseButtonPress", 0)
    for index in range(1, 50_001):
        plugin.input("MouseMove", index, index)

    assert not plugin.state.can_stop
    assert plugin.state.sample_count == 50_000
    plugin.replay()
    assert len(plugin.painted[0].points) == 50_000
