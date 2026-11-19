import pytest

from krita_brush_replay.domain.brush_samples import CompactBrushSamples
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    ResourceRef,
    Stroke,
    normalized_input_speed,
)

BRUSH_STATE = BrushState(
    preset=ResourceRef("preset", "Basic", "basic.kpp"),
    foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
    background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
    size=10.0,
    opacity=1.0,
    flow=1.0,
    rotation=0.0,
    pattern=ResourceRef("pattern", "Canvas", "canvas.pat"),
    gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
    blending_mode="normal",
)


def test_brush_sample_rejects_pressure_outside_unit_interval() -> None:
    with pytest.raises(ValueError, match="pressure"):
        BrushSample(x=1.0, y=2.0, pressure=1.1, timestamp_ms=0)


def test_stroke_requires_at_least_one_sample() -> None:
    with pytest.raises(ValueError, match="sample"):
        Stroke(samples=(), brush_state=BRUSH_STATE)


def test_context_requires_document_and_layer_ids() -> None:
    with pytest.raises(ValueError, match="document_id"):
        ContextRef(document_id="", layer_id="layer")


def test_brush_sample_rejects_speed_outside_unit_interval() -> None:
    with pytest.raises(ValueError, match="speed"):
        BrushSample(x=1.0, y=2.0, pressure=0.5, timestamp_ms=0, speed=-0.1)


def test_normalized_input_speed_uses_distance_and_elapsed_time() -> None:
    speed = normalized_input_speed(
        (0.0, 0.0),
        10,
        (30.0, 0.0),
        12,
        max_speed_pixels_per_ms=30.0,
    )

    assert speed == 0.5


def test_normalized_input_speed_is_zero_without_elapsed_time() -> None:
    assert normalized_input_speed((0.0, 0.0), 10, (30.0, 0.0), 10) == 0.0


@pytest.mark.parametrize("count", [0, 1, 1003])
def test_sample_serialization_preserves_every_axis_and_timestamp_exactly(count: int) -> None:
    timestamps = (0, 0, 2**32 - 1, 2**32, 3, 2**63 - 1)
    samples = tuple(
        BrushSample(
            x=index / 13,
            y=-1e200 if index % 2 else 1e-200,
            pressure=index % 101 / 100,
            timestamp_ms=timestamps[index % len(timestamps)],
            x_tilt=-0.0 if index % 2 else 0.0,
            y_tilt=index / 17,
            rotation=index / 19,
            tangential_pressure=-index / 23,
            speed=index % 97 / 96,
        )
        for index in range(count)
    )

    restored = CompactBrushSamples.from_bytes(bytes(CompactBrushSamples(iter(samples))))

    assert tuple(restored) == samples
    assert [sample.x_tilt.hex() for sample in restored] == [
        sample.x_tilt.hex() for sample in samples
    ]
    assert restored == CompactBrushSamples(samples)
    assert hash(restored) == hash(CompactBrushSamples(samples))
    for selection in (slice(None, None, -1), slice(3, 900, 7), slice(800, 20, -3)):
        assert restored[selection] == samples[selection]
    for index in (0, -1, count // 2):
        if samples:
            assert restored[index] == samples[index]
    with pytest.raises(IndexError):
        restored[count]


@pytest.mark.parametrize("damage", ["unknown_format", "truncated", "corrupted", "trailing_data"])
def test_incomplete_or_damaged_sample_recordings_are_rejected(damage: str) -> None:
    encoded = bytes(
        CompactBrushSamples(BrushSample(index / 7, 1, 0.5, index) for index in range(1003))
    )
    if damage == "unknown_format":
        damaged = b"unknown" + encoded
    elif damage == "truncated":
        damaged = encoded[:-1]
    elif damage == "corrupted":
        damaged = bytearray(encoded)
        damaged[len(encoded) // 2] ^= 1
        damaged = bytes(damaged)
    else:
        damaged = encoded + b"incomplete"

    with pytest.raises(ValueError):
        CompactBrushSamples.from_bytes(damaged)


def test_sample_equality_and_hashing_treat_signed_zeros_as_equal() -> None:
    positive = CompactBrushSamples((BrushSample(0.0, 0.0, 0.0, 0),))
    negative = CompactBrushSamples((BrushSample(-0.0, -0.0, -0.0, 0),))

    assert positive == negative
    assert hash(positive) == hash(negative)
