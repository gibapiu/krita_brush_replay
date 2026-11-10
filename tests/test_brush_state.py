import pytest

from krita_brush_replay.infrastructure.krita_brush_state import KritaBrushStateAdapter


class FakeResource:
    def __init__(self, resource_type: str, name: str, filename: str) -> None:
        self._type = resource_type
        self._name = name
        self._filename = filename

    def type(self) -> str:
        return self._type

    def name(self) -> str:
        return self._name

    def filename(self) -> str:
        return self._filename


class FakeColor:
    def __init__(self, model: str, depth: str, profile: str) -> None:
        self._model = model
        self._depth = depth
        self._profile = profile
        self._components: list[float] = []

    def colorModel(self) -> str:
        return self._model

    def colorDepth(self) -> str:
        return self._depth

    def colorProfile(self) -> str:
        return self._profile

    def components(self) -> list[float]:
        return list(self._components)

    def setComponents(self, values: list[float]) -> bool:
        self._components = list(values)
        return True


RAMP = FakeResource("gradients", "Ramp", "ramp.svg")


def color(components: tuple[float, ...]) -> FakeColor:
    result = FakeColor("RGBA", "U8", "sRGB")
    result.setComponents(list(components))
    return result


class FakeView:
    def __init__(
        self,
        preset: FakeResource,
        pattern: FakeResource,
        gradient: FakeResource | None = None,
    ) -> None:
        self.preset = preset
        self.foreground = color((0.1, 0.2, 0.3, 1.0))
        self.background = color((0.8, 0.7, 0.6, 1.0))
        self.size = 17.0
        self.opacity = 0.4
        self.flow = 0.6
        self.rotation = 32.0
        self.pattern = pattern
        self.gradient = gradient or RAMP
        self.blending_mode = "multiply"
        self.ignore_blending_mode = False
        self.readback_drift = 0.0

    def currentBrushPreset(self):
        return self.preset

    def foregroundColor(self):
        return self.foreground

    def backgroundColor(self):
        return self.background

    def brushSize(self) -> float:
        return self.size + self.readback_drift

    def paintingOpacity(self) -> float:
        return self.opacity + self.readback_drift

    def paintingFlow(self) -> float:
        return self.flow + self.readback_drift

    def brushRotation(self) -> float:
        return self.rotation + self.readback_drift

    def currentPattern(self):
        return self.pattern

    def currentGradient(self):
        return self.gradient

    def currentBlendingMode(self) -> str:
        return self.blending_mode

    def setCurrentBrushPreset(self, value) -> None:
        self.preset = value

    def setForeGroundColor(self, value) -> None:
        self.foreground = value
        self._drift_color(self.foreground)

    def setBackGroundColor(self, value) -> None:
        self.background = value
        self._drift_color(self.background)

    def setBrushSize(self, value: float) -> None:
        self.size = value

    def setPaintingOpacity(self, value: float) -> None:
        self.opacity = value

    def setPaintingFlow(self, value: float) -> None:
        self.flow = value

    def setBrushRotation(self, value: float) -> None:
        self.rotation = value

    def setCurrentPattern(self, value) -> None:
        self.pattern = value

    def setCurrentGradient(self, value) -> None:
        self.gradient = value

    def setCurrentBlendingMode(self, value: str) -> None:
        if not self.ignore_blending_mode:
            self.blending_mode = value

    def _drift_color(self, value: FakeColor) -> None:
        if self.readback_drift:
            components = value.components()
            components[0] += self.readback_drift
            value.setComponents(components)


class FakeKrita:
    def __init__(self, resources: tuple[FakeResource, ...]) -> None:
        self._resources = (*resources, RAMP)
        self.requests: list[str] = []

    def resources(self, resource_type: str) -> dict[str, FakeResource]:
        self.requests.append(resource_type)
        reported_type = {
            "preset": "paintoppresets",
            "pattern": "patterns",
            "gradient": "gradients",
        }.get(resource_type)
        if reported_type is None:
            return {}
        return {
            resource.name(): resource
            for resource in self._resources
            if resource.type() == reported_type
        }


def test_all_supported_brush_settings_round_trip() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    other_preset = FakeResource("paintoppresets", "Pencil", "pencil.kpp")
    other_pattern = FakeResource("patterns", "Canvas", "canvas.pat")
    other_gradient = FakeResource("gradients", "Mesh", "mesh.svg")
    view = FakeView(preset, pattern)
    recording_adapter = KritaBrushStateAdapter(FakeKrita(()), FakeColor)
    recorded = recording_adapter.capture(view)
    other_recorded = recording_adapter.capture(
        FakeView(other_preset, other_pattern, other_gradient)
    )

    view.preset = other_preset
    view.pattern = other_pattern
    view.gradient = other_gradient
    view.size = 99.0
    view.opacity = 1.0
    view.flow = 1.0
    view.rotation = 0.0
    view.blending_mode = "normal"
    view.foreground = color((1.0, 1.0, 1.0, 1.0))
    view.background = color((0.0, 0.0, 0.0, 1.0))
    krita = FakeKrita((preset, pattern, other_preset, other_pattern, other_gradient))
    replay_adapter = KritaBrushStateAdapter(krita, FakeColor)
    apply = replay_adapter.prepare_replay(view, (recorded, other_recorded)).apply
    apply(recorded)

    assert replay_adapter.capture(view) == recorded
    assert sorted(krita.requests) == ["gradient", "pattern", "preset"]


def test_captured_resource_is_reused_when_another_resource_has_the_same_name() -> None:
    captured_preset = FakeResource("paintoppresets", "Ink", "bundle-a/ink.kpp")
    hidden_preset = FakeResource("paintoppresets", "Ink", "bundle-b/ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(captured_preset, pattern)
    krita = FakeKrita((captured_preset, hidden_preset, pattern))
    adapter = KritaBrushStateAdapter(krita, FakeColor)
    recorded = adapter.capture(view)
    view.preset = hidden_preset

    adapter.prepare_replay(view, (recorded,)).apply(recorded)

    assert view.preset is captured_preset
    assert sorted(krita.requests) == ["gradient", "pattern", "preset"]


def test_fresh_resource_replaces_a_stale_captured_object() -> None:
    stale_preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    fresh_preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(stale_preset, pattern)
    adapter = KritaBrushStateAdapter(
        FakeKrita((fresh_preset, pattern)),
        FakeColor,
    )
    recorded = adapter.capture(view)

    adapter.prepare_replay(view, (recorded,)).apply(recorded)

    assert view.preset is fresh_preset


def test_preparing_a_comparison_replay_keeps_the_current_preset_untouched() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(preset, pattern)
    selected_presets = []
    view.setCurrentBrushPreset = selected_presets.append
    adapter = KritaBrushStateAdapter(FakeKrita((preset, pattern)), FakeColor)

    adapter.prepare_replay(view, ())

    assert selected_presets == []


def test_deleted_resource_is_rejected_before_an_applier_is_returned() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(preset, pattern)
    adapter = KritaBrushStateAdapter(FakeKrita(()), FakeColor)
    recorded = adapter.capture(view)

    with pytest.raises(RuntimeError, match="unavailable"):
        adapter.prepare_replay(view, (recorded,))


def test_apply_reports_a_setting_that_krita_silently_rejects() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(preset, pattern)
    adapter = KritaBrushStateAdapter(FakeKrita((preset, pattern)), FakeColor)
    recorded = adapter.capture(view)
    view.blending_mode = "normal"
    view.ignore_blending_mode = True
    apply = adapter.prepare_replay(view, (recorded,)).apply

    with pytest.raises(RuntimeError, match="blending mode"):
        apply(recorded)


def test_apply_accepts_harmless_numeric_readback_drift() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(preset, pattern)
    adapter = KritaBrushStateAdapter(FakeKrita((preset, pattern)), FakeColor)
    recorded = adapter.capture(view)
    view.readback_drift = 0.5e-6

    adapter.prepare_replay(view, (recorded,)).apply(recorded)

    assert adapter.capture(view) != recorded


def test_apply_rejects_numeric_readback_outside_tolerance() -> None:
    preset = FakeResource("paintoppresets", "Ink", "ink.kpp")
    pattern = FakeResource("patterns", "Paper", "paper.pat")
    view = FakeView(preset, pattern)
    adapter = KritaBrushStateAdapter(FakeKrita((preset, pattern)), FakeColor)
    recorded = adapter.capture(view)
    view.readback_drift = 2e-6
    apply = adapter.prepare_replay(view, (recorded,)).apply

    with pytest.raises(
        RuntimeError,
        match="foreground colour, background colour, opacity, flow",
    ):
        apply(recorded)


def test_brush_choices_list_the_resources_krita_offers() -> None:
    ink = FakeResource("paintoppresets", "ink", "ink.kpp")
    airbrush = FakeResource("paintoppresets", "Airbrush", "airbrush.kpp")
    paper = FakeResource("patterns", "Paper", "paper.pat")
    adapter = KritaBrushStateAdapter(FakeKrita((ink, paper, airbrush)), FakeColor)

    choices = adapter.brush_choices()

    assert [preset.name for preset in choices.presets] == ["Airbrush", "ink"]
    assert [pattern.name for pattern in choices.patterns] == ["Paper"]
    assert [gradient.name for gradient in choices.gradients] == ["Ramp"]
    assert "normal" in choices.blending_modes
