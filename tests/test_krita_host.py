from krita_brush_replay.domain.recording import (
    BrushSample,
    ContextRef,
    RecordingSession,
    Stroke,
)
from krita_brush_replay.infrastructure.krita_host import KritaHost

CONTEXT = ContextRef("document", "layer")


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

    def replay(self, *args) -> None:
        self.calls.append(args)


def test_replay_passes_the_explicit_target_to_the_replayer() -> None:
    document = FakeDocument(FakeNode())
    host = KritaHost(FakeKrita(document))
    replayer = FakeReplayer()
    host._replayer = replayer
    session = RecordingSession(
        CONTEXT,
        (Stroke((BrushSample(1.0, 2.0, 0.5, 0),)),),
    )

    host.replay(session, CONTEXT, lambda: None, lambda error: None)

    assert len(replayer.calls) == 1
    assert replayer.calls[0][0:4] == (document, document.node, session, CONTEXT)
