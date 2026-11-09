from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    ContextRef,
    RecordingSession,
    ResourceRef,
    Stroke,
)
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

    def activeNode(self) -> FakeNode:
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
    def inverted(self):
        return self, True

    def __mul__(self, other):
        return self

    def map(self, point) -> FakePoint:
        return FakePoint(point.x() + 10.0, point.y() + 20.0)


class FakeView:
    def flakeToCanvasTransform(self) -> FakeTransform:
        return FakeTransform()

    def flakeToImageTransform(self) -> FakeTransform:
        return FakeTransform()


class FakeWindow:
    def __init__(self) -> None:
        self.view = FakeView()

    def activeView(self) -> FakeView:
        return self.view


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
        self.applied = []

    def capture(self, view) -> BrushState:
        return self.current

    def apply(self, view, state: BrushState) -> None:
        self.applied.append(state)
        self.current = state


def test_replay_passes_the_explicit_target_to_the_replayer() -> None:
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
        CONTEXT,
        lambda: None,
        lambda error: None,
        use_original_settings=True,
    )

    assert len(replayer.calls) == 1
    args, kwargs = replayer.calls[0]
    assert args[0:4] == (document, document.node, session, CONTEXT)
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
        CONTEXT,
        lambda: None,
        errors.append,
        use_original_settings=True,
    )

    assert settings.applied == [recorded_state, saved_state]
    assert settings.current == saved_state
    assert len(errors) == 1
    assert "paint failed" in str(errors[0])
