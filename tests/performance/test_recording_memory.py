"""Opt-in memory budgets for active strokes and recording history."""

import tracemalloc

from krita_brush_replay.app.service import BrushReplayService
from krita_brush_replay.domain.recording import BrushSample, InputPhase
from tests.support.recording import ReplayHost, draw


def test_long_active_stroke_stays_compact_during_capture_and_stop():
    """Long active stroke stays compact during capture and stop."""

    service = BrushReplayService(ReplayHost())
    service.start_recording()
    count = 20_000
    tracemalloc.start()
    try:
        service.accept_input(InputPhase.PRESS, BrushSample(0, 4, 0.5, 0))
        for index in range(1, count):
            service.accept_input(
                InputPhase.MOVE, BrushSample(index / 13, 4, index % 101 / 100, index * 8)
            )
        drawing_bytes, _ = tracemalloc.get_traced_memory()
        service.stop_recording()
        retained_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert drawing_bytes < count * 40
    assert retained_bytes < count * 40
    assert peak_bytes < count * 60
    assert service.state.sample_count == count
    assert service.session.strokes[0].samples[-1] == BrushSample(
        (count - 1) / 13, 4, (count - 1) % 101 / 100, (count - 1) * 8
    )


def test_many_strokes_and_discarded_history_remain_within_recording_budget():
    """Many strokes and discarded history remain within recording budget."""

    service = BrushReplayService(ReplayHost())
    service.start_recording()
    tracemalloc.start()
    try:
        for stroke in range(20):
            service.accept_input(InputPhase.PRESS, BrushSample(stroke, 4, 0.5, 0))
            for index in range(1, 1000):
                service.accept_input(InputPhase.MOVE, BrushSample(index / 13, 4, 0.5, index * 8))
            service.accept_input(InputPhase.RELEASE, None)
        retained_bytes, _ = tracemalloc.get_traced_memory()
        for _ in range(20):
            service.undo_triggered()
        draw(service, 123)
        service.stop_recording()
        after_branch, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert retained_bytes < 20_000 * 40
    assert after_branch < 100_000
    assert service.state.sample_count == 1
    assert service.session.strokes[0].samples[0].x == 123
