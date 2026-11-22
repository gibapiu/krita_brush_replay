"""Opt-in memory budgets for long strokes and large recordings."""

import tracemalloc

from krita_brush_replay.domain.recording import BrushSample, InputPhase
from tests.support.recording import ReplayHost


def test_a_long_active_stroke_has_a_fixed_memory_budget(service_factory):
    """Growing a stroke tenfold does not retain its samples in memory."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    measured = []
    count = 200_000
    tracemalloc.start()
    try:
        service.accept_input(InputPhase.PRESS, BrushSample(0, 4, 0.5, 0))
        for index in range(1, count):
            service.accept_input(
                InputPhase.MOVE, BrushSample(index / 13, 4, index % 101 / 100, index * 8)
            )
            if index in (19_999, count - 1):
                measured.append(tracemalloc.get_traced_memory()[0])
        service.stop_recording()
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert measured[1] < measured[0] + 500_000
    assert peak < 4_000_000
    assert retained < 500_000
    service.replay()
    assert service.state.sample_count == count
    assert host.outputs[0].strokes[0].samples[-1] == BrushSample(
        (count - 1) / 13, 4, (count - 1) % 101 / 100, (count - 1) * 8
    )


def test_a_large_recording_stays_within_its_memory_budget(service_factory):
    """More than thirty thousand strokes retain a small recording footprint."""

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    retained = []
    tracemalloc.start()
    try:
        for index in range(32_000):
            service.accept_input(InputPhase.PRESS, BrushSample(index, 4, 0.5, index * 8))
            for point in range(1, 4):
                service.accept_input(
                    InputPhase.MOVE, BrushSample(index + point / 13, 4, 0.7, index * 8 + point)
                )
            service.accept_input(InputPhase.RELEASE, None)
            if index in (7_999, 31_999):
                retained.append(tracemalloc.get_traced_memory()[0])
        service.stop_recording()
        final, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert retained[1] < retained[0] + 1_500_000
    assert peak < 8_000_000
    assert final < 1_500_000
    service.replay()
    assert service.state.stroke_count == 32_000
    assert service.state.sample_count == 128_000
    assert host.outputs[0].strokes[-1].samples[-1].x == 31_999 + 3 / 13
