"""Opt-in latency budget for bulk stroke edits and reset."""

from time import perf_counter

from krita_brush_replay.app.service import BrushReplayService
from tests.support.recording import ReplayHost, draw


def test_bulk_edit_and_reset_retains_the_result_within_interaction_budget():
    """Bulk edit and reset retains the result within interaction budget."""

    service = BrushReplayService(ReplayHost())
    service.start_recording()
    for index in range(2000):
        draw(service, index)
    service.stop_recording()
    indexes = range(2000)
    started = perf_counter()
    for size in range(1, 11):
        service.override_setting(indexes, "size", float(size))
    edited = service.state
    service.reset_setting(indexes, "size")
    elapsed = perf_counter() - started

    assert elapsed < 1.0
    assert all(stroke.effective.size == 10.0 for stroke in edited.strokes)
    assert all(stroke.effective.size == 12.345 for stroke in service.state.strokes)
