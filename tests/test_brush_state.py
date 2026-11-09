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


def color(components: tuple[float, ...]) -> FakeColor:
    result = FakeColor("RGBA", "U8", "sRGB")
    result.setComponents(list(components))
    return result


class FakeView:
    def __init__(self, preset: FakeResource, pattern: FakeResource) -> None:
        self.preset = preset
        self.foreground = color((0.1, 0.2, 0.3, 1.0))
        self.background = color((0.8, 0.7, 0.6, 1.0))
        self.size = 17.0
        self.opacity = 0.4
        self.flow = 0.6
        self.rotation = 32.0
        self.pattern = pattern
        self.blending_mode = "multiply"

    def currentBrushPreset(self):
        return self.preset

    def foregroundColor(self):
        return self.foreground

    def backgroundColor(self):
        return self.background

    def brushSize(self) -> float:
        return self.size

    def paintingOpacity(self) -> float:
        return self.opacity

    def paintingFlow(self) -> float:
        return self.flow

    def brushRotation(self) -> float:
        return self.rotation

    def currentPattern(self):
        return self.pattern

    def currentBlendingMode(self) -> str:
        return self.blending_mode

    def setCurrentBrushPreset(self, value) -> None:
        self.preset = value

    def setForeGroundColor(self, value) -> None:
        self.foreground = value

    def setBackGroundColor(self, value) -> None:
        self.background = value

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

    def setCurrentBlendingMode(self, value: str) -> None:
        self.blending_mode = value


class FakeKrita:
    def __init__(self, resources: tuple[FakeResource, ...]) -> None:
        self._resources = resources

    def resources(self, resource_type: str) -> dict[str, FakeResource]:
        return {
            resource.name(): resource
            for resource in self._resources
            if resource.type() == resource_type
        }


def test_all_supported_brush_settings_round_trip() -> None:
    preset = FakeResource("preset", "Ink", "ink.kpp")
    pattern = FakeResource("pattern", "Paper", "paper.pat")
    other_preset = FakeResource("preset", "Pencil", "pencil.kpp")
    other_pattern = FakeResource("pattern", "Canvas", "canvas.pat")
    view = FakeView(preset, pattern)
    adapter = KritaBrushStateAdapter(
        FakeKrita((preset, pattern, other_preset, other_pattern)),
        FakeColor,
    )
    recorded = adapter.capture(view)

    view.preset = other_preset
    view.pattern = other_pattern
    view.size = 99.0
    view.opacity = 1.0
    view.flow = 1.0
    view.rotation = 0.0
    view.blending_mode = "normal"
    view.foreground = color((1.0, 1.0, 1.0, 1.0))
    view.background = color((0.0, 0.0, 0.0, 1.0))
    adapter.apply(view, recorded)

    assert adapter.capture(view) == recorded
