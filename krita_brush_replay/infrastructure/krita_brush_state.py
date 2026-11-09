"""Capture and restore the brush settings exposed by Krita's public API."""

from __future__ import annotations

from collections.abc import Callable

from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef

ColorFactory = Callable[[str, str, str], object]


class KritaBrushStateAdapter:
    """Translate between immutable brush state and a live Krita view."""

    def __init__(self, krita_instance, color_factory: ColorFactory | None = None) -> None:
        self._krita = krita_instance
        self._color_factory = color_factory

    def capture(self, view) -> BrushState:
        return BrushState(
            preset=_capture_resource(view.currentBrushPreset(), "preset"),
            foreground=_capture_color(view.foregroundColor()),
            background=_capture_color(view.backgroundColor()),
            size=float(view.brushSize()),
            opacity=float(view.paintingOpacity()),
            flow=float(view.paintingFlow()),
            rotation=float(view.brushRotation()),
            pattern=_capture_resource(view.currentPattern(), "pattern"),
            blending_mode=str(view.currentBlendingMode()),
        )

    def apply(self, view, state: BrushState) -> None:
        preset = self._find_resource(state.preset)
        pattern = self._find_resource(state.pattern)
        foreground = self._create_color(state.foreground)
        background = self._create_color(state.background)

        view.setCurrentBrushPreset(preset)
        view.setForeGroundColor(foreground)
        view.setBackGroundColor(background)
        view.setBrushSize(state.size)
        view.setPaintingOpacity(state.opacity)
        view.setPaintingFlow(state.flow)
        view.setBrushRotation(state.rotation)
        view.setCurrentPattern(pattern)
        view.setCurrentBlendingMode(state.blending_mode)

    def _find_resource(self, reference: ResourceRef):
        resources = self._krita.resources(reference.resource_type)
        resource = resources.get(reference.name)
        if resource is not None and str(resource.filename()) == reference.filename:
            return resource
        raise RuntimeError(
            f"recorded {reference.resource_type} resource is unavailable: {reference.name}"
        )

    def _create_color(self, state: ColorState):
        factory = self._color_factory
        if factory is None:
            from krita import ManagedColor

            factory = ManagedColor
        color = factory(state.model, state.depth, state.profile)
        if color.setComponents(list(state.components)) is False:
            raise RuntimeError("Krita could not restore a recorded colour")
        return color


def _capture_resource(resource, expected_type: str) -> ResourceRef:
    if resource is None or str(resource.type()) != expected_type:
        raise RuntimeError(f"Krita did not provide an active {expected_type} resource")
    return ResourceRef(
        resource_type=expected_type,
        name=str(resource.name()),
        filename=str(resource.filename()),
    )


def _capture_color(color) -> ColorState:
    if color is None:
        raise RuntimeError("Krita did not provide an active colour")
    return ColorState(
        model=str(color.colorModel()),
        depth=str(color.colorDepth()),
        profile=str(color.colorProfile()),
        components=tuple(float(value) for value in color.components()),
    )
