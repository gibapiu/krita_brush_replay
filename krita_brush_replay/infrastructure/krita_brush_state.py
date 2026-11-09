"""Capture and restore the brush settings exposed by Krita's public API."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef

ColorFactory = Callable[[str, str, str], object]
BrushStateApplier = Callable[[BrushState], None]
_RESOURCE_TYPE_NAMES = {
    "preset": ("paintoppresets", "preset"),
    "pattern": ("patterns", "pattern"),
}


class KritaBrushStateAdapter:
    """Translate between immutable brush state and a live Krita view."""

    def __init__(self, krita_instance, color_factory: ColorFactory | None = None) -> None:
        self._krita = krita_instance
        self._color_factory = color_factory
        self._captured_resources: dict[ResourceRef, object] = {}

    def capture(self, view) -> BrushState:
        preset = self._capture_resource(view.currentBrushPreset(), "preset")
        pattern = self._capture_resource(view.currentPattern(), "pattern")
        return BrushState(
            preset=preset,
            foreground=_capture_color(view.foregroundColor()),
            background=_capture_color(view.backgroundColor()),
            size=float(view.brushSize()),
            opacity=float(view.paintingOpacity()),
            flow=float(view.paintingFlow()),
            rotation=float(view.brushRotation()),
            pattern=pattern,
            blending_mode=str(view.currentBlendingMode()),
        )

    def prepare_replay(
        self,
        view,
        states: Iterable[BrushState],
    ) -> BrushStateApplier:
        """Resolve all resources before painting and return a checked applier."""

        resources = self._resolve_resources(states)

        def apply(state: BrushState) -> None:
            self._apply(view, state, resources)
            if self.capture(view) != state:
                raise RuntimeError("Krita could not apply all recorded brush settings")

        return apply

    def _apply(self, view, state: BrushState, resources: dict[ResourceRef, object]) -> None:
        preset = resources[state.preset]
        pattern = resources[state.pattern]
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

    def _capture_resource(self, resource, resource_type: str) -> ResourceRef:
        reference = _capture_resource(resource, resource_type)
        self._captured_resources[reference] = resource
        return reference

    def _resolve_resources(
        self,
        states: Iterable[BrushState],
    ) -> dict[ResourceRef, object]:
        references = {
            reference
            for state in states
            for reference in (state.preset, state.pattern)
        }
        resolved: dict[ResourceRef, object] = {}

        for resource_type in _RESOURCE_TYPE_NAMES:
            required = {
                reference
                for reference in references
                if reference.resource_type == resource_type
            }
            if not required:
                continue
            _reported_type, lookup_type = _RESOURCE_TYPE_NAMES[resource_type]
            available_by_identity = {
                (str(resource.name()), str(resource.filename())): resource
                for resource in self._krita.resources(lookup_type).values()
            }
            available_names = {name for name, _filename in available_by_identity}
            for reference in required:
                cached = self._captured_resources.get(reference)
                if reference.name in available_names and cached is not None:
                    if _resource_matches(cached, reference):
                        resolved[reference] = cached
                        continue
                resource = available_by_identity.get(
                    (reference.name, reference.filename)
                )
                if resource is not None:
                    resolved[reference] = resource

        missing = references - resolved.keys()
        if missing:
            reference = min(
                missing,
                key=lambda item: (item.resource_type, item.name, item.filename),
            )
            raise RuntimeError(
                f"recorded {reference.resource_type} resource is unavailable: "
                f"{reference.name}"
            )
        return resolved

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
    reported_type, _lookup_type = _RESOURCE_TYPE_NAMES[expected_type]
    if resource is None or str(resource.type()) != reported_type:
        raise RuntimeError(f"Krita did not provide an active {expected_type} resource")
    return ResourceRef(
        resource_type=expected_type,
        name=str(resource.name()),
        filename=str(resource.filename()),
    )


def _resource_matches(resource, reference: ResourceRef) -> bool:
    return (
        str(resource.name()) == reference.name
        and str(resource.filename()) == reference.filename
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
