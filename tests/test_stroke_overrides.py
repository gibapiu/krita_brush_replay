import sys
from time import perf_counter
from types import ModuleType

import pytest

from krita_brush_replay.app.service import BrushReplayService, ServiceMode
from krita_brush_replay.domain.recording import BrushSample, InputPhase
from krita_brush_replay.infrastructure.krita_host import KritaHost
from krita_brush_replay.infrastructure.qt_facade import QtCore


class Resource:
    def __init__(
        self,
        resource_type: str,
        name: str,
        size: float = 10.0,
        *,
        accepts_size: bool = True,
    ) -> None:
        self._type = resource_type
        self._name = name
        self.size = size
        self.accepts_size = accepts_size

    def type(self) -> str:
        return self._type

    def name(self) -> str:
        return self._name

    def filename(self) -> str:
        return f"{self._name.lower()}.res"


class ManagedColor:
    def __init__(self, model: str, depth: str, profile: str) -> None:
        self._space = (model, depth, profile)
        self._components = [0.0, 0.0, 0.0, 1.0]

    def colorModel(self) -> str:
        return self._space[0]

    def colorDepth(self) -> str:
        return self._space[1]

    def colorProfile(self) -> str:
        return self._space[2]

    def components(self) -> list[float]:
        return list(self._components)

    def setComponents(self, values: list[float]) -> bool:
        self._components = list(values)
        return True


class Resources:
    def __init__(self) -> None:
        self.ink = Resource("paintoppresets", "Ink", size=12.0)
        self.pencil = Resource("paintoppresets", "Pencil", size=3.0)
        self.airbrush = Resource("paintoppresets", "Airbrush", size=80.0)
        self.webs = Resource("paintoppresets", "Webs", size=1.0, accepts_size=False)
        self.paper = Resource("patterns", "Paper")
        self.ramp = Resource("gradients", "Ramp")

    def all(self) -> tuple[Resource, ...]:
        return (self.ink, self.pencil, self.airbrush, self.webs, self.paper, self.ramp)


class View:
    def __init__(self, resources: Resources) -> None:
        self.preset = resources.airbrush
        self.size = resources.airbrush.size
        self.opacity = 1.0
        self.flow = 1.0
        self.rotation = 0.0
        self.foreground = ManagedColor("RGBA", "U8", "sRGB")
        self.background = ManagedColor("RGBA", "U8", "sRGB")
        self.pattern = resources.paper
        self.gradient = resources.ramp
        self.blending_mode = "normal"

    def currentBrushPreset(self):
        return self.preset

    def setCurrentBrushPreset(self, preset) -> None:
        self.preset = preset
        self.size = preset.size

    def brushSize(self) -> float:
        return self.size

    def setBrushSize(self, size: float) -> None:
        if self.preset.accepts_size:
            self.size = size
            self.preset.size = size

    def paintingOpacity(self) -> float:
        return self.opacity

    def setPaintingOpacity(self, opacity: float) -> None:
        self.opacity = opacity

    def paintingFlow(self) -> float:
        return self.flow

    def setPaintingFlow(self, flow: float) -> None:
        self.flow = flow

    def brushRotation(self) -> float:
        return self.rotation

    def setBrushRotation(self, rotation: float) -> None:
        self.rotation = rotation

    def foregroundColor(self):
        return self.foreground

    def setForeGroundColor(self, color) -> None:
        self.foreground = color

    def backgroundColor(self):
        return self.background

    def setBackGroundColor(self, color) -> None:
        self.background = color

    def currentPattern(self):
        return self.pattern

    def setCurrentPattern(self, pattern) -> None:
        self.pattern = pattern

    def currentGradient(self):
        return self.gradient

    def setCurrentGradient(self, gradient) -> None:
        self.gradient = gradient

    def currentBlendingMode(self) -> str:
        return self.blending_mode

    def setCurrentBlendingMode(self, mode: str) -> None:
        self.blending_mode = mode


class Layer:
    def __init__(self, view: View, layer_id: str = "layer") -> None:
        self._view = view
        self._id = layer_id
        self.painted: list[tuple[str, float, float]] = []

    def uniqueId(self) -> str:
        return self._id

    def paintAbility(self) -> str:
        return "PAINT"

    def paintPolylineStroke(self, points) -> None:
        view = self._view
        self.painted.append((view.preset.name(), view.size, view.opacity))


class Document:
    def __init__(self, layer: Layer) -> None:
        self.layer = layer
        self.root = Layer(layer._view, "document")

    def activeNode(self) -> Layer:
        return self.layer

    def rootNode(self) -> Layer:
        return self.root

    def tryBarrierLock(self) -> bool:
        return True

    def unlock(self) -> None:
        pass


class Window:
    def __init__(self, view: View) -> None:
        self.view = view

    def activeView(self) -> View:
        return self.view


class Krita:
    def __init__(self) -> None:
        self.presets = Resources()
        self.view = View(self.presets)
        self.layer = Layer(self.view)
        self.document = Document(self.layer)
        self.window = Window(self.view)

    def activeDocument(self) -> Document:
        return self.document

    def activeWindow(self) -> Window:
        return self.window

    def resources(self, resource_type: str) -> dict[str, Resource]:
        reported_type = {
            "preset": "paintoppresets",
            "pattern": "patterns",
            "gradient": "gradients",
        }[resource_type]
        return {
            resource.name(): resource
            for resource in self.presets.all()
            if resource.type() == reported_type
        }


class ImmediateTimer:
    @staticmethod
    def singleShot(_delay_ms: int, callback) -> None:
        callback()


@pytest.fixture
def krita(monkeypatch) -> Krita:
    module = ModuleType("krita")
    module.ManagedColor = ManagedColor
    module.StrokePoint = lambda *values: values
    monkeypatch.setitem(sys.modules, "krita", module)
    monkeypatch.setattr(QtCore, "QTimer", ImmediateTimer, raising=False)
    return Krita()


def record(service: BrushReplayService, krita: Krita, presets: list[Resource]) -> None:
    service.start_recording()
    for index, preset in enumerate(presets):
        krita.view.setCurrentBrushPreset(preset)
        service.accept_input(InputPhase.PRESS, BrushSample(float(index), 0.0, 0.5, 0))
        service.accept_input(InputPhase.RELEASE, None)
    service.stop_recording()


def test_replacing_a_stroke_preset_keeps_its_recorded_size(krita: Krita) -> None:
    host = KritaHost(krita)
    service = BrushReplayService(host)
    presets = krita.presets
    record(service, krita, [presets.ink, presets.ink])
    pencil = next(ref for ref in host.brush_choices().presets if ref.name == "Pencil")
    krita.view.setCurrentBrushPreset(presets.airbrush)

    service.override_setting((1,), "preset", pencil)
    service.replay()

    assert service.state.mode is ServiceMode.IDLE
    assert krita.layer.painted == [("Ink", 12.0, 1.0), ("Pencil", 12.0, 1.0)]
    assert (krita.view.preset, krita.view.size) == (presets.airbrush, 80.0)


def test_preset_with_a_fixed_size_paints_with_the_size_its_engine_allows(krita: Krita) -> None:
    host = KritaHost(krita)
    service = BrushReplayService(host)
    record(service, krita, [krita.presets.ink])
    webs = next(ref for ref in host.brush_choices().presets if ref.name == "Webs")

    service.override_setting((0,), "preset", webs)
    service.replay()

    assert krita.layer.painted == [("Webs", 1.0, 1.0)]
    assert "complete" in service.state.message.lower()


def test_replay_leaves_every_touched_preset_as_it_was(krita: Krita) -> None:
    service = BrushReplayService(KritaHost(krita))
    presets = krita.presets
    record(service, krita, [presets.ink, presets.pencil])
    krita.view.setCurrentBrushPreset(presets.airbrush)

    service.override_setting((0, 1), "size", 40.0)
    service.replay()

    assert [preset.size for preset in (presets.ink, presets.pencil, presets.airbrush)] == [
        12.0,
        3.0,
        80.0,
    ]


def test_bulk_override_with_individual_reset_reaches_the_canvas(krita: Krita) -> None:
    service = BrushReplayService(KritaHost(krita))
    presets = krita.presets
    record(service, krita, [presets.ink, presets.pencil, presets.ink])

    service.override_setting((0, 1, 2), "size", 40.0)
    service.override_setting((0, 1, 2), "opacity", 0.25)
    service.reset_setting((1,), "size")
    service.replay()

    assert krita.layer.painted == [
        ("Ink", 40.0, 0.25),
        ("Pencil", 3.0, 0.25),
        ("Ink", 40.0, 0.25),
    ]


def test_bulk_editing_a_long_recording_stays_interactive(krita: Krita) -> None:
    service = BrushReplayService(KritaHost(krita))
    record(service, krita, [krita.presets.ink] * 2_000)
    every_stroke = range(service.state.stroke_count)
    started = perf_counter()

    for size in range(1, 11):
        service.override_setting(every_stroke, "size", float(size))
    service.reset_setting(every_stroke, "size")

    assert perf_counter() - started < 1.0
