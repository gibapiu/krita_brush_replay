"""Lossless sample storage through its public codec."""

import pytest

from krita_brush_replay.domain.brush_samples import CompactBrushSamples
from krita_brush_replay.domain.recording import BrushSample


def test_a_long_tablet_stroke_survives_compaction_and_serialization_exactly() -> None:
    """A long tablet stroke survives compaction and serialization exactly."""

    samples = tuple(
        BrushSample(
            x=120.25 + index / 13,
            y=240.5 - index / 17,
            pressure=(index % 81 + 10) / 100,
            timestamp_ms=42_000 + index * 8,
            x_tilt=-25.0 + index % 20 / 7,
            y_tilt=12.0 - index % 30 / 11,
            rotation=index % 360 / 3,
            tangential_pressure=(index % 21 - 10) / 10,
            speed=index % 97 / 96,
        )
        for index in range(1_003)
    )

    restored = CompactBrushSamples.from_bytes(bytes(CompactBrushSamples(iter(samples))))

    assert tuple(restored) == samples
    assert len(restored) == len(samples)
    assert restored[0] == samples[0]
    assert restored[-1] == samples[-1]


def test_truncated_sample_data_is_rejected_instead_of_silently_losing_input() -> None:
    """Truncated sample data is rejected instead of silently losing input."""

    samples = CompactBrushSamples(BrushSample(index / 7, 4, 0.5, index * 8) for index in range(400))
    with pytest.raises(ValueError):
        CompactBrushSamples.from_bytes(bytes(samples)[:-7])
