"""Opt-in panel latency and allocation budgets for large recordings."""

import tracemalloc
from time import perf_counter

import pytest


@pytest.mark.qt
def test_recording_growth_keeps_the_panel_within_its_update_budget(qapp, workbench):
    """A visible docker stays responsive past thirty thousand strokes."""

    bench = workbench
    started = perf_counter()
    bench.click("Record new session")
    for index in range(32_000):
        bench.stroke(index)
        if index % 128 == 0:
            qapp.processEvents()
    bench.click("Stop")
    qapp.processEvents()
    assert perf_counter() - started < 8.0
    assert bench.message_contains("32000 strokes")
    assert bench.button("Replay").isEnabled()


@pytest.mark.qt
def test_bulk_edit_and_status_updates_keep_the_panel_responsive(workbench, monkeypatch):
    """Editing a large selection uses little temporary memory and survives status updates."""

    bench = workbench
    bench.record(32_000)
    bench.click("Select all strokes")
    tracemalloc.start()
    try:
        bench.number("Size", 40)
        _retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8_000_000
    with monkeypatch.context() as unavailable_canvas:
        unavailable_canvas.setattr(bench.krita, "document", None)
        started = perf_counter()
        for _ in range(10):
            bench.click("Record new session")
        assert perf_counter() - started < 0.1
        assert bench.message_contains("Open a document")
    assert bench.field("Size").value() == 40
    assert bench.field("Reset size to the recorded value").isEnabled()
    assert bench.button("Replay").isEnabled()
