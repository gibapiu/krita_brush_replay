"""Replay destinations, settings, pressure, completion, and failure recovery."""

import pytest

from krita_brush_replay.domain.recording import BrushSample


@pytest.mark.qt
def test_record_then_replay_uses_saved_settings_and_transformed_input_on_active_layer(workbench):
    """Record then replay uses saved settings and transformed input on active layer."""

    from tests.support.krita import Layer

    bench = workbench
    krita = bench.krita
    krita.view.transform.translate(10, 20)
    krita.view.transform.scale(2, 2)
    bench.click("Record new session")
    bench.stroke()
    krita.view.setCurrentBrushPreset(krita.pencil)
    bench.stroke(80)
    bench.click("Stop")
    original = krita.document.layer
    target = krita.document.layer = Layer(krita, "destination")
    krita.view.setBrushSize(90)

    bench.replay()

    assert original.writes == []
    assert [state for state, _ in target.writes] == [
        {
            "preset": "ink.kpp",
            "size": 12.345,
            "opacity": 0.8,
            "flow": 0.6,
            "rotation": 32.0,
            "foreground": (0.1, 0.2, 0.3, 1.0),
            "background": (0.8, 0.7, 0.6, 1.0),
            "pattern": "paper.pat",
            "gradient": "ramp.svg",
            "blending_mode": "multiply",
        },
        {
            "preset": "pencil.kpp",
            "size": 3.0,
            "opacity": 0.8,
            "flow": 0.6,
            "rotation": 32.0,
            "foreground": (0.1, 0.2, 0.3, 1.0),
            "background": (0.8, 0.7, 0.6, 1.0),
            "pattern": "paper.pat",
            "gradient": "ramp.svg",
            "blending_mode": "multiply",
        },
    ]
    points = target.writes[0][1]
    assert [(p.position.x(), p.position.y(), p.pressure, p.time, p.speed) for p in points] == [
        (10.0, 10.0, 1.0, 0.0, 0.0),
        (25.0, 10.0, 1.0, 10.0, 0.1),
    ]
    assert krita.view.size == 90
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_comparison_ignores_edits_and_keeps_current_settings(workbench):
    """Comparison ignores edits and keeps current settings."""

    bench = workbench
    bench.record()
    bench.number("Size", 40)
    bench.krita.view.setCurrentBrushPreset(bench.krita.pencil)
    bench.krita.view.setBrushSize(75)
    current = bench.krita.view.snapshot()

    bench.replay(compare=True)

    assert bench.krita.document.layer.writes[0][0] == current
    assert bench.krita.view.snapshot() == current
    bench.replay()
    assert bench.krita.document.layer.writes[1][0]["size"] == 40


@pytest.mark.qt
@pytest.mark.parametrize("compare", [False, True])
def test_current_global_pressure_mapping_is_used_once_without_changing_recording(
    workbench, compare
):
    """Current global pressure mapping is used once without changing recording."""

    from tests.support.krita import tablet

    bench = workbench
    bench.click("Record new session")
    tablet(bench.krita.canvas, "Press", pressure=0.5)
    tablet(bench.krita.canvas, "Release")
    bench.click("Stop")
    bench.krita.curve = "0,0;0.5,0.8;1,1;"
    bench.replay(compare=compare)
    bench.krita.curve = "0,0;0.5,0.3;1,1;"
    bench.replay(compare=compare)
    bench.krita.pressure_enabled = False
    bench.replay(compare=compare)
    bench.krita.pressure_enabled = True
    bench.krita.curve = "0,0;1,1;"
    bench.replay(compare=compare)

    assert [points[0].pressure for _, points in bench.krita.document.layer.writes] == [
        0.8,
        0.3,
        1.0,
        0.5,
    ]


@pytest.mark.qt
@pytest.mark.parametrize("failure", ["painting", "context", "rejected-setting"])
def test_partial_replay_failure_restores_active_and_touched_presets_and_can_retry(
    workbench, failure
):
    """Partial replay failure restores active and touched presets and can retry."""

    from tests.support.krita import Layer

    bench = workbench
    krita = bench.krita
    bench.click("Record new session")
    bench.stroke()
    krita.view.setCurrentBrushPreset(krita.pencil)
    bench.stroke(90)
    bench.click("Stop")
    bench.number("Size", 40)
    krita.view.setCurrentBrushPreset(krita.fixed)
    saved = krita.view.snapshot()
    target = krita.document.layer

    def fail_next():
        if failure == "painting":
            target.failure = "paint failed"
        elif failure == "context":
            krita.document.layer = Layer(krita, "another-layer")
        else:
            krita.view.blending = "normal"
            krita.view.reject_blending = True

    krita.after_paint = fail_next
    bench.replay()

    assert len(target.writes) == 1
    assert bench.message_contains("failed")
    assert (krita.ink.size, krita.pencil.size, krita.fixed.size) == (12.345, 3.0, 1.0)
    if failure != "rejected-setting":
        assert krita.view.snapshot() == saved
    else:
        assert bench.message_contains("could not be restored")
    krita.after_paint = None
    target.failure = None
    krita.document.layer = target
    krita.view.reject_blending = False
    krita.view.blending = saved["blending_mode"]
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_busy_replay_keeps_controls_disabled_until_host_finishes_without_blocking_qt(workbench):
    """Busy replay keeps controls disabled until host finishes without blocking qt."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore
    from tests.support.workbench import wait_until

    bench = workbench
    bench.record()
    bench.krita.document.busy = True
    bench.click("Replay")
    wait_until(lambda: bench.krita.document.completion_checked)
    assert not bench.button("Record new session").isEnabled()
    assert not bench.button("Replay").isEnabled()
    assert not bench.field("Size").isEnabled()
    responsive = []
    QtCore.QTimer.singleShot(0, lambda: responsive.append(True))
    wait_until(lambda: bool(responsive))
    assert bench.message_contains("replaying")

    bench.krita.document.busy = False
    wait_until(lambda: bench.button("Replay").isEnabled())
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_unavailable_resources_stop_before_painting_and_can_be_repaired(workbench):
    """Unavailable resources stop before painting and can be repaired."""

    bench = workbench
    bench.record()
    bench.krita.available.remove(bench.krita.ink)
    bench.replay()
    assert bench.krita.document.layer.writes == []
    assert bench.message_contains("unavailable")
    bench.krita.available.append(bench.krita.ink)
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
@pytest.mark.parametrize("unavailable", ["paint-layer", "pressure-setting"])
def test_missing_host_capability_reports_a_useful_error_without_painting(workbench, unavailable):
    """Missing host capability reports a useful error without painting."""

    bench = workbench
    bench.record()
    if unavailable == "paint-layer":
        bench.krita.document.layer.writable = False
    else:
        bench.krita.pressure_available = False
    bench.replay()
    assert bench.krita.document.layer.writes == []
    assert bench.message_contains("paint layer" if unavailable == "paint-layer" else "pen pressure")
    bench.krita.document.layer.writable = True
    bench.krita.pressure_available = True
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_same_named_resources_do_not_change_the_recorded_preset_identity(workbench):
    """Same named resources do not change the recorded preset identity."""

    from tests.support.krita import Resource

    bench = workbench
    bench.record()
    impostor = Resource("paintoppresets", "Ink", "other-bundle/ink.kpp")
    bench.krita.available.append(impostor)
    bench.krita.view.setCurrentBrushPreset(impostor)
    bench.replay()
    assert bench.krita.document.layer.writes[0][0]["preset"] == "ink.kpp"
    assert bench.krita.view.preset.filename() == "other-bundle/ink.kpp"


@pytest.mark.qt
@pytest.mark.parametrize("compare", [False, True])
def test_fallback_delivers_drag_and_tap_with_raw_axes_and_correct_settings(workbench, compare):
    """Fallback delivers drag and tap with raw axes and correct settings."""

    from tests.support.krita import Layer, tablet

    bench = workbench
    krita = bench.krita
    krita.document.layer = Layer(krita, "fallback", native=False)
    bench.click("Record new session")
    tablet(krita.canvas, "Press", pressure=0.25, timestamp=100)
    tablet(krita.canvas, "Move", x=60, pressure=0.75, timestamp=110)
    tablet(krita.canvas, "Release", x=60, timestamp=120)
    tablet(krita.canvas, "Press", x=90, pressure=0.5, timestamp=130)
    tablet(krita.canvas, "Release", x=90, timestamp=140)
    bench.click("Stop")
    bench.number("Size", 40)
    krita.view.setCurrentBrushPreset(krita.pencil)
    krita.view.setBrushSize(75)
    saved = krita.view.snapshot()
    krita.curve = "0,0;0.5,0.3;1,1;"
    krita.pen_initialization = True
    krita.canvas.gestures.clear()
    krita.canvas.received.clear()

    bench.replay(compare=compare)

    gestures = krita.canvas.gestures
    assert len(gestures) == 2
    assert [event[0] for event in gestures[0][1]] == ["press", "move", "release"]
    assert [event[0] for event in gestures[1][1]] == ["press", "release"]
    assert [event[1:5] for event in gestures[0][1]] == [
        (30, 40, 0.25, -8),
        (60, 40, 0.75, -8),
        (60, 40, 0, -8),
    ]
    assert gestures[1][1][0][3] == 0.5
    assert gestures[0][1][0][5:7] == (14, 35.0)
    assert gestures[0][1][0][7] == pytest.approx(0.6, abs=1e-7)
    assert [state["size"] for state, _ in gestures] == ([75, 75] if compare else [40, 40])
    assert krita.canvas.received[-1] == "leave"
    assert krita.view.snapshot() == saved
    assert krita.canvas.hasFocus()
    assert bench.message_contains("complete")


@pytest.mark.qt
@pytest.mark.parametrize("change", ["layer", "transform", "canvas-switch", "delivery"])
def test_fallback_interruption_releases_original_canvas_before_reporting_failure(
    workbench, monkeypatch, change
):
    """Fallback interruption releases original canvas before reporting failure."""

    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import Layer

    bench = workbench
    krita = bench.krita
    target = krita.document.layer = Layer(krita, "fallback", native=False)
    bench.record()
    krita.canvas.received.clear()
    krita.canvas.gestures.clear()
    saved = krita.view.snapshot()

    def change_target():
        if change == "layer":
            krita.document.layer = Layer(krita, "another-layer", native=False)
        elif change == "transform":
            krita.view.transform.translate(10, 0)
        elif change == "canvas-switch":
            bench.runtime.canvas_changed()
        else:
            post = QtCore.QCoreApplication.postEvent

            def fail_next_move(receiver, event):
                if event.type() == qt_enum(QtCore.QEvent, "Type", "TabletMove"):
                    monkeypatch.setattr(QtCore.QCoreApplication, "postEvent", post)
                    raise RuntimeError("canvas input delivery unavailable")
                post(receiver, event)

            monkeypatch.setattr(QtCore.QCoreApplication, "postEvent", fail_next_move)

    krita.canvas.on_press = change_target
    bench.replay()

    assert bench.message_contains("failed")
    assert len(krita.canvas.gestures) == 1
    assert [event[0] for event in krita.canvas.gestures[0][1]] == ["press", "release"]
    assert krita.canvas.received[-1] == "leave"
    assert krita.canvas.active is None
    assert krita.view.snapshot() == saved
    krita.canvas.on_press = None
    krita.document.layer = target
    krita.view.transform.reset()
    bench.record()
    krita.canvas.gestures.clear()
    bench.replay()
    assert bench.message_contains("complete")
    assert len(krita.canvas.gestures) == 1


@pytest.mark.qt
def test_replay_timeout_reports_failure_restores_settings_and_allows_retry(workbench, monkeypatch):
    """An unfinished replay eventually reports failure, restores settings, and permits retry."""

    from krita_brush_replay.infrastructure import krita_replay
    from tests.support.workbench import wait_until

    bench = workbench
    bench.record()
    clock = [0.0]
    monkeypatch.setattr(krita_replay, "monotonic", lambda: clock[0])
    bench.krita.document.busy = True
    saved = bench.krita.view.snapshot()
    bench.click("Replay")
    wait_until(lambda: bench.krita.document.completion_checked)
    clock[0] = 24 * 60 * 60
    wait_until(lambda: bench.button("Record new session").isEnabled())
    assert bench.message_contains("did not finish")
    assert bench.krita.view.snapshot() == saved
    bench.krita.document.busy = False
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_a_long_stroke_replays_without_dropping_samples_or_accumulating_delay(
    workbench, monkeypatch
):
    """A long gesture retains every point and its duration despite slower delivery callbacks."""

    from krita_brush_replay.infrastructure import synthetic_input
    from krita_brush_replay.infrastructure.krita_host import KritaHost
    from tests.support.replay_clock import DelayedDelivery, VirtualClock

    clock = VirtualClock()
    monkeypatch.setattr(synthetic_input, "monotonic", lambda: clock.now_ms / 1000)
    krita = workbench.krita
    host = KritaHost(krita)
    host.capture_mapper(krita.canvas)
    delivery = DelayedDelivery(clock)
    replayer = synthetic_input.SyntheticInputReplayer(
        host.replay_input_target, clock, event_poster=delivery
    )
    samples = tuple(
        BrushSample(30 + index / 13, 40 + index / 17, 0.5, 1000 + index * 10)
        for index in range(1003)
    )
    completed, errors = [], []
    replayer.replay_stroke(
        replayer.prepare(),
        samples,
        lambda: None,
        lambda: completed.append(krita.canvas.received[-1]),
        errors.append,
    )
    clock.drain()

    assert errors == []
    assert completed == ["leave"]
    gesture = krita.canvas.gestures[0][1]
    assert tuple((event[1], event[2], event[3]) for event in gesture[:-1]) == tuple(
        (sample.x, sample.y, sample.pressure) for sample in samples
    )
    first_move_time = delivery.stroke_move_times[0]
    assert [when - first_move_time for when in delivery.stroke_move_times] == pytest.approx(
        [sample.timestamp_ms - samples[1].timestamp_ms for sample in samples[1:]],
        rel=0,
        abs=1,
    )
