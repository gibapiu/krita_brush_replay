"""Opt-in latency budget for bulk stroke edits and reset."""

from time import perf_counter

from tests.support.recording import ReplayHost, draw


def test_bulk_edit_and_reset_retains_the_result_within_interaction_budget(service_factory):
    """Bulk edit and reset retains the result within interaction budget."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    for index in range(32_000):
        draw(service, index)
    service.stop_recording()
    indexes = range(32_000)
    started = perf_counter()
    for size in (20, 30, 40):
        service.override_setting(indexes, "size", float(size))
    elapsed = perf_counter() - started
    service.replay()
    assert all(stroke.brush_state.size == 40 for stroke in host.outputs[-1].strokes)
    started = perf_counter()
    service.reset_setting(indexes, "size")
    elapsed += perf_counter() - started
    service.replay()
    assert elapsed < 1.0
    assert all(stroke.brush_state.size == 12.345 for stroke in host.outputs[-1].strokes)
