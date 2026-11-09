import pytest

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
