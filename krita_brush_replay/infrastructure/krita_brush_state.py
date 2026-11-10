"""Capture and restore the brush settings exposed by Krita's public API."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import isclose

from krita_brush_replay.domain.brush_override import BrushChoices
from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef

ColorFactory = Callable[[str, str, str], object]
BrushStateApplier = Callable[[BrushState], None]
_RESOURCE_TYPE_NAMES = {
    "preset": ("paintoppresets", "preset"),
    "pattern": ("patterns", "pattern"),
    "gradient": ("gradients", "gradient"),
}
_FLOAT_ABS_TOLERANCE = 1e-6
BLENDING_MODE_LABELS = {
    "normal": "Normal",
    "multiply": "Multiply",
    "screen": "Screen",
    "overlay": "Overlay",
    "darken": "Darken",
    "lighten": "Lighten",
    "burn": "Burn",
    "dodge": "Color Dodge",
    "linear_burn": "Linear Burn",
    "linear_dodge": "Linear Dodge",
    "soft_light": "Soft Light (Photoshop)",
    "hard_light": "Hard Light",
    "add": "Addition",
    "subtract": "Subtract",
    "diff": "Difference",
    "hue": "Hue",
    "saturation": "Saturation",
    "color": "Color",
    "luminize": "Luminosity",
    "behind": "Behind",
    "erase": "Erase",
}


@dataclass(frozen=True, slots=True)
class PreparedBrushSettings:
    saved_state: BrushState
    apply: BrushStateApplier
    restore: Callable[[], None]


class KritaColorCodec:
    def to_qcolor(self, state: ColorState):
        return _create_color(_managed_color_type(), state).colorForCanvas(None)

    def from_qcolor(self, qcolor) -> ColorState:
        return _capture_color(_managed_color_type().fromQColor(qcolor))


class KritaBrushStateAdapter:
    """Translate between immutable brush state and a live Krita view."""

    def __init__(self, krita_instance, color_factory: ColorFactory | None = None) -> None:
        self._krita = krita_instance
        self._color_factory = color_factory
        self._captured_resources: dict[ResourceRef, object] = {}

    def capture(self, view) -> BrushState:
        preset = self._capture_resource(view.currentBrushPreset(), "preset")
        pattern = self._capture_resource(view.currentPattern(), "pattern")
        gradient = self._capture_resource(view.currentGradient(), "gradient")
        return BrushState(
            preset=preset,
            foreground=_capture_color(view.foregroundColor()),
            background=_capture_color(view.backgroundColor()),
            size=float(view.brushSize()),
            opacity=float(view.paintingOpacity()),
            flow=float(view.paintingFlow()),
            rotation=float(view.brushRotation()),
            pattern=pattern,
            gradient=gradient,
            blending_mode=str(view.currentBlendingMode()),
        )

    def brush_choices(self) -> BrushChoices:
        return BrushChoices(
            presets=self._available_resources("preset"),
            patterns=self._available_resources("pattern"),
            gradients=self._available_resources("gradient"),
            blending_modes=BLENDING_MODE_LABELS,
        )

    def prepare_replay(
        self,
        view,
        states: Sequence[BrushState],
    ) -> PreparedBrushSettings:
        saved_state = self.capture(view)
        resources = self._resolve_resources((saved_state, *states))

        def apply(state: BrushState) -> None:
            self._apply(view, state, resources)
            mismatches = _brush_state_mismatches(self.capture(view), state)
            if mismatches:
                raise RuntimeError(
                    "Krita did not accept brush settings: "
                    + ", ".join(mismatches)
                )

        preset_baselines = self._preset_baselines(view, states, saved_state, resources)

        def restore() -> None:
            try:
                for baseline in preset_baselines:
                    apply(baseline)
            finally:
                apply(saved_state)

        return PreparedBrushSettings(saved_state, apply, restore)

    def _preset_baselines(
        self,
        view,
        states: Sequence[BrushState],
        saved_state: BrushState,
        resources: dict[ResourceRef, object],
    ) -> tuple[BrushState, ...]:
        other_presets = dict.fromkeys(
            state.preset for state in states if state.preset != saved_state.preset
        )
        if not other_presets:
            return ()
        try:
            baselines = []
            for preset in other_presets:
                view.setCurrentBrushPreset(resources[preset])
                baselines.append(self.capture(view))
            return tuple(baselines)
        finally:
            self._apply(view, saved_state, resources)

    def _apply(self, view, state: BrushState, resources: dict[ResourceRef, object]) -> None:
        preset = resources[state.preset]
        pattern = resources[state.pattern]
        gradient = resources[state.gradient]
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
        view.setCurrentGradient(gradient)
        view.setCurrentBlendingMode(state.blending_mode)

    def _available_resources(self, resource_type: str) -> tuple[ResourceRef, ...]:
        _reported_type, lookup_type = _RESOURCE_TYPE_NAMES[resource_type]
        references = {
            ResourceRef(resource_type, str(resource.name()), str(resource.filename()))
            for resource in self._krita.resources(lookup_type).values()
            if resource.name()
        }
        return tuple(sorted(references, key=lambda reference: reference.name.lower()))

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
            for reference in (state.preset, state.pattern, state.gradient)
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
                resource = available_by_identity.get(
                    (reference.name, reference.filename)
                )
                if resource is not None:
                    resolved[reference] = resource
                    continue
                cached = self._captured_resources.get(reference)
                if reference.name in available_names and cached is not None:
                    resolved[reference] = cached

        missing = references - resolved.keys()
        if missing:
            reference = min(
                missing,
                key=lambda item: (item.resource_type, item.name, item.filename),
            )
            raise RuntimeError(
                f"{reference.resource_type} resource is unavailable: "
                f"{reference.name}"
            )
        return resolved

    def _create_color(self, state: ColorState):
        return _create_color(self._color_factory or _managed_color_type(), state)


def _managed_color_type() -> ColorFactory:
    from krita import ManagedColor

    return ManagedColor


def _create_color(factory: ColorFactory, state: ColorState):
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


def _brush_state_mismatches(
    actual: BrushState,
    expected: BrushState,
) -> tuple[str, ...]:
    mismatches = []
    exact_settings = (
        ("preset", actual.preset, expected.preset),
        ("pattern", actual.pattern, expected.pattern),
        ("gradient", actual.gradient, expected.gradient),
        ("blending mode", actual.blending_mode, expected.blending_mode),
    )
    numeric_settings = (
        ("opacity", actual.opacity, expected.opacity),
        ("flow", actual.flow, expected.flow),
    )
    mismatches.extend(
        name
        for name, actual_value, expected_value in exact_settings
        if actual_value != expected_value
    )
    if not _colors_match(actual.foreground, expected.foreground):
        mismatches.append("foreground colour")
    if not _colors_match(actual.background, expected.background):
        mismatches.append("background colour")
    mismatches.extend(
        name
        for name, actual_value, expected_value in numeric_settings
        if not isclose(
            actual_value,
            expected_value,
            rel_tol=0.0,
            abs_tol=_FLOAT_ABS_TOLERANCE,
        )
    )
    return tuple(mismatches)


def _colors_match(actual: ColorState, expected: ColorState) -> bool:
    return (
        actual.model == expected.model
        and actual.depth == expected.depth
        and actual.profile == expected.profile
        and len(actual.components) == len(expected.components)
        and all(
            isclose(
                actual_value,
                expected_value,
                rel_tol=0.0,
                abs_tol=_FLOAT_ABS_TOLERANCE,
            )
            for actual_value, expected_value in zip(
                actual.components,
                expected.components,
            )
        )
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
