import pytest

from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    RecordingSession,
    ResourceRef,
    Stroke,
)
from krita_brush_replay.infrastructure.krita_brush_state import PreparedBrushSettings
from krita_brush_replay.infrastructure.krita_host import KritaHost

CONTEXT = ContextRef("document", "layer")


def brush_state(size: float = 10.0) -> BrushState:
    return BrushState(
        preset=ResourceRef("preset", f"Brush {size}", f"brush-{size}.kpp"),
        foreground=ColorState("RGBA", "U8", "sRGB", (0.0, 0.0, 0.0, 1.0)),
        background=ColorState("RGBA", "U8", "sRGB", (1.0, 1.0, 1.0, 1.0)),
        size=size,
        opacity=1.0,
        flow=1.0,
        rotation=0.0,
        pattern=ResourceRef("pattern", "Canvas", "canvas.pat"),
        gradient=ResourceRef("gradient", "Ramp", "ramp.svg"),
        blending_mode="normal",
    )


class FakeNode:
    def __init__(self, node_id: str = "layer") -> None:
        self.node_id = node_id

    def uniqueId(self) -> str:
        return self.node_id

    def paintAbility(self) -> str:
        return "PAINT"


class FakeDocument:
    def __init__(self, node: FakeNode) -> None:
        self.node = node
        self.root = FakeNode("document")
        self.active_node_calls = 0

    def activeNode(self) -> FakeNode:
        self.active_node_calls += 1
        return self.node

    def rootNode(self) -> FakeNode:
        return self.root


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self._x = x
        self._y = y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class FakeTransform:
    def __init__(self, revision: int = 0) -> None:
        self.revision = revision

    def inverted(self):
        return self, True

    def __mul__(self, other):
        return self

    def map(self, point) -> FakePoint:
        return FakePoint(point.x() + 10.0, point.y() + 20.0)

    def __eq__(self, other) -> bool:
        return isinstance(other, FakeTransform) and self.revision == other.revision


class FakeView:
    def __init__(self, revision: int = 0) -> None:
        self.revision = revision

    def flakeToCanvasTransform(self) -> FakeTransform:
        return FakeTransform(self.revision)

    def flakeToImageTransform(self) -> FakeTransform:
        return FakeTransform(self.revision)


class FakeWindow:
    def __init__(self) -> None:
        self.view = FakeView()
        self.widget = object()

    def activeView(self) -> FakeView:
        return self.view

    def qwindow(self):
        return self.widget


class FakeKrita:
    def __init__(self, document: FakeDocument) -> None:
        self.document = document
        self.window = FakeWindow()

    def activeDocument(self) -> FakeDocument:
        return self.document

    def activeWindow(self) -> FakeWindow:
        return self.window


def test_active_context_uses_stable_document_and_layer_ids() -> None:
    host = KritaHost(FakeKrita(FakeDocument(FakeNode())))

    assert host.active_context() == CONTEXT


def test_active_context_rejects_objects_without_stable_ids() -> None:
    node = FakeNode()
    node.uniqueId = None
    host = KritaHost(FakeKrita(FakeDocument(node)))

    assert host.active_context() is None


def test_capture_mapper_maps_input_without_querying_view_again() -> None:
    host = KritaHost(FakeKrita(FakeDocument(FakeNode())))
    to_image = host.capture_mapper()

    assert to_image((2.0, 3.0)) == (12.0, 23.0)


class FakeMetaObject:
    def className(self) -> str:
        return "KisCanvasWidget"


class FakeCanvas:
    def __init__(self, window) -> None:
        self._window = window
        self.focused = 0

    def window(self):
        return self._window

    def parentWidget(self):
        return None

    def metaObject(self) -> FakeMetaObject:
        return FakeMetaObject()

    def objectName(self) -> str:
        return "canvas"

    def isVisible(self) -> bool:
        return True

    def isEnabled(self) -> bool:
        return True

    def setFocus(self, *_reason) -> None:
        self.focused += 1


def test_replay_input_target_caches_receiver_transform_and_focus() -> None:
    krita = FakeKrita(FakeDocument(FakeNode()))
    host = KritaHost(krita)
    canvas = FakeCanvas(krita.window.widget)

    assert host.is_canvas_target(canvas)
    target = host.replay_input_target()
    target.focus()

    assert target.receiver is canvas
    assert target.to_widget((2.0, 3.0)) == (12.0, 23.0)
    assert target.is_valid()
    assert canvas.focused == 1


class FreshViewWindow(FakeWindow):
    def __init__(self) -> None:
        super().__init__()
        self.revision = 0

    def activeView(self) -> FakeView:
        return FakeView(self.revision)


def test_replay_target_accepts_fresh_wrappers_for_the_same_view() -> None:
    krita = FakeKrita(FakeDocument(FakeNode()))
    krita.window = FreshViewWindow()
    host = KritaHost(krita)
    canvas = FakeCanvas(krita.window.widget)

    assert host.is_canvas_target(canvas)
    target = host.replay_input_target()

    assert target.is_valid()


def test_replay_target_rejects_a_changed_canvas_transform() -> None:
    krita = FakeKrita(FakeDocument(FakeNode()))
    krita.window = FreshViewWindow()
    host = KritaHost(krita)
    canvas = FakeCanvas(krita.window.widget)
    assert host.is_canvas_target(canvas)
    target = host.replay_input_target()

    krita.window.revision = 1

    assert not target.is_valid()


class FakeReplayer:
    def __init__(self) -> None:
        self.calls = []

    def replay(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))
        args[4]()


class FailingReplayer:
    def replay(self, *args, **kwargs) -> None:
        kwargs["apply_brush_state"](args[2].strokes[0].brush_state)
        args[5](RuntimeError("paint failed"))


class FakeBrushSettings:
    def __init__(self) -> None:
        self.current = brush_state(99.0)
        self.capture_calls = 0
        self.prepared = []
        self.applied = []

    def capture(self, view) -> BrushState:
        self.capture_calls += 1
        return self.current

    def prepare_replay(self, view, states):
        self.prepared = list(states)
        saved_state = self.current

        def apply(state: BrushState) -> None:
            self.applied.append(state)
            self.current = state

        return PreparedBrushSettings(saved_state, apply, lambda: apply(saved_state))


class MissingResourceBrushSettings(FakeBrushSettings):
    def prepare_replay(self, view, states):
        raise RuntimeError("recorded preset resource is unavailable: Missing")


def test_replay_resolves_the_active_target_once() -> None:
    document = FakeDocument(FakeNode())
    host = KritaHost(FakeKrita(document))
    replayer = FakeReplayer()
    settings = FakeBrushSettings()
    host._replayer = replayer
    host._brush_settings = settings
    session = RecordingSession(
        CONTEXT,
        (Stroke((BrushSample(1.0, 2.0, 0.5, 0),), brush_state()),),
    )

    host.replay(
        session,
        lambda: None,
        lambda error: None,
        use_original_settings=True,
    )

    assert len(replayer.calls) == 1
    args, kwargs = replayer.calls[0]
    assert args[0:4] == (document, document.node, session, CONTEXT)
    assert document.active_node_calls == 1
    assert callable(kwargs["apply_brush_state"])
    assert settings.applied == [brush_state(99.0)]


def test_replay_restores_pre_replay_settings_after_failure() -> None:
    document = FakeDocument(FakeNode())
    host = KritaHost(FakeKrita(document))
    settings = FakeBrushSettings()
    saved_state = settings.current
    host._brush_settings = settings
    host._replayer = FailingReplayer()
    recorded_state = brush_state(20.0)
    session = RecordingSession(
        CONTEXT,
        (Stroke((BrushSample(1.0, 2.0, 0.5, 0),), recorded_state),),
    )
    errors = []

    host.replay(
        session,
        lambda: None,
        errors.append,
        use_original_settings=True,
    )

    assert settings.applied == [recorded_state, saved_state]
    assert settings.current == saved_state
    assert len(errors) == 1
    assert "paint failed" in str(errors[0])


def test_original_settings_replay_resolves_resources_before_painting() -> None:
    document = FakeDocument(FakeNode())
    host = KritaHost(FakeKrita(document))
    replayer = FakeReplayer()
    host._brush_settings = MissingResourceBrushSettings()
    host._replayer = replayer
    session = RecordingSession(
        CONTEXT,
        (Stroke((BrushSample(1.0, 2.0, 0.5, 0),), brush_state()),),
    )
    errors = []

    host.replay(
        session,
        lambda: None,
        errors.append,
        use_original_settings=True,
    )

    assert replayer.calls == []
    assert len(errors) == 1
    assert "unavailable" in str(errors[0])


def test_current_settings_replay_keeps_settings_unchanged_without_reselecting_preset() -> None:
    document = FakeDocument(FakeNode())
    host = KritaHost(FakeKrita(document))
    replayer = FakeReplayer()
    settings = FakeBrushSettings()
    host._brush_settings = settings
    host._replayer = replayer
    session = RecordingSession(
        CONTEXT,
        (Stroke((BrushSample(1.0, 2.0, 0.5, 0),), brush_state()),),
    )

    host.replay(
        session,
        lambda: None,
        lambda error: None,
        use_original_settings=False,
    )

    assert settings.prepared == []
    assert settings.applied == []
    assert callable(replayer.calls[0][1]["apply_brush_state"])


def test_replay_target_rejects_a_canvas_switch_in_the_same_window() -> None:
    krita = FakeKrita(FakeDocument(FakeNode()))
    host = KritaHost(krita)
    assert host.is_canvas_target(FakeCanvas(krita.window.widget))
    target = host.replay_input_target()

    host.invalidate_canvas_target()

    assert not target.is_valid()
    with pytest.raises(RuntimeError, match="record a stroke"):
        host.replay_input_target()


def test_current_settings_survive_first_pen_device_initialization() -> None:
    host = KritaHost(FakeKrita(FakeDocument(FakeNode())))
    settings = FakeBrushSettings()
    saved_state = settings.current
    host._brush_settings = settings

    class DeviceSwitchingReplayer:
        def replay(self, *args, **kwargs) -> None:
            # Krita can select the pen's preset when it sees the first hover.
            settings.current = brush_state(5.0)
            kwargs["apply_brush_state"](args[2].strokes[0].brush_state)
            assert settings.current == saved_state
            args[4]()

    host._replayer = DeviceSwitchingReplayer()
    errors = []
    host.replay(
        RecordingSession(CONTEXT, (Stroke((BrushSample(1, 2, 0.5, 0),), brush_state()),)),
        lambda: None,
        errors.append,
        use_original_settings=False,
    )

    assert settings.applied == [saved_state]
    assert settings.current == saved_state
    assert errors == []
