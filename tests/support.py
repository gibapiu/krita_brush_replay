
from __future__ import annotations

from collections import deque, namedtuple
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

from krita_brush_replay.app.recording_workspace import RecordingWorkspace
from krita_brush_replay.app.recording_library import RecordingBrowser
from krita_brush_replay.app.service import BrushReplayService
from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.storage.background_writer import SerialWorker
from krita_brush_replay.infrastructure.host.krita_host import KritaHost
from krita_brush_replay.infrastructure.host.krita_replay import KritaReplayer
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, qt_enum
from krita_brush_replay.infrastructure.storage.recording_archive import RecordingArchive
from krita_brush_replay.infrastructure.storage.recording_library import FileRecordingLibrary
from krita_brush_replay.infrastructure.storage.sample_budget import SampleBudget
from krita_brush_replay.infrastructure.host.synthetic_input import SyntheticInputReplayer
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.errors import report_issue
from krita_brush_replay.observability.persistence import ObservedArchive, ObservedWorker
from krita_brush_replay.observability.replay import ObservedReplayer
from krita_brush_replay.runtime import BrushReplayRuntime

WORKER = ObservedWorker(SerialWorker())
LIBRARY_WORKER = ObservedWorker(SerialWorker())
BUDGET = SampleBudget()
NativePoint = namedtuple(
    "NativePoint", "position pressure x_tilt y_tilt rotation tangential_pressure time speed"
)


class Resource:
    def __init__(
        self,
        resource_type: str,
        name: str,
        size: float = 10.0,
    ) -> None:
        self._type = resource_type
        self._name = name
        self.size = size

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

    def snapshot(self):
        return (*self._space, tuple(self._components))


class Resources:
    def __init__(self) -> None:
        self.ink = Resource("paintoppresets", "Ink", size=12.0)
        self.pencil = Resource("paintoppresets", "Pencil", size=3.0)
        self.airbrush = Resource("paintoppresets", "Airbrush", size=80.0)
        self.paper = Resource("patterns", "Paper")
        self.canvas = Resource("patterns", "Canvas")
        self.ramp = Resource("gradients", "Ramp")
        self.mesh = Resource("gradients", "Mesh")

    def all(self) -> tuple[Resource, ...]:
        return (
            self.ink, self.pencil, self.airbrush,
            self.paper, self.canvas, self.ramp, self.mesh,
        )


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
        self.image_offset = (10, 20)

    def currentBrushPreset(self):
        return self.preset

    def setCurrentBrushPreset(self, preset) -> None:
        self.preset = preset
        self.size = preset.size

    def brushSize(self) -> float:
        return self.size

    def setBrushSize(self, size: float) -> None:
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

    def flakeToCanvasTransform(self):
        return Transform()

    def flakeToImageTransform(self):
        return Transform(*self.image_offset)

    def snapshot(self):
        return {
            "preset": self.preset.name(),
            "size": self.size,
            "opacity": self.opacity,
            "flow": self.flow,
            "rotation": self.rotation,
            "foreground": self.foreground.snapshot(),
            "background": self.background.snapshot(),
            "pattern": self.pattern.name(),
            "gradient": self.gradient.name(),
            "blending_mode": self.blending_mode,
        }


class LinearCurve:
    def __init__(self, text: str) -> None:
        self._points = [tuple(map(float, point.split(","))) for point in text.split(";") if point]

    def floatTransfer(self, size: int) -> list[float]:
        return [self._at(index / (size - 1)) for index in range(size)]

    def _at(self, x: float) -> float:
        (x0, y0), (x1, y1) = next(
            pair for pair in zip(self._points, self._points[1:]) if x <= pair[1][0]
        )
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    @staticmethod
    def interpolateLinear(pressure: float, transfer: list[float]) -> float:
        position = pressure * (len(transfer) - 1)
        left = int(position)
        right = min(left + 1, len(transfer) - 1)
        return transfer[left] + (transfer[right] - transfer[left]) * (position - left)


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class Panel(QtCore.QObject):
    def __init__(self):
        super().__init__()
        for name in (
            "record", "stop", "replay", "replay_current", "override", "reset",
            "source", "delete", "refresh",
        ):
            setattr(self, name + "_requested", Signal())
        if not hasattr(self, "destroyed"):
            self.destroyed = Signal()

    def apply_state(self, state):
        self.state = state

    def apply_library_state(self, state):
        self.library_state = state

    def set_choices(self, choices):
        self.choices = choices

    def show_message(self, message):
        self.message = message


@dataclass(frozen=True)
class Transform:
    dx: float = 0
    dy: float = 0

    def inverted(self):
        return Transform(-self.dx, -self.dy), True

    def __mul__(self, other):
        return Transform(self.dx + other.dx, self.dy + other.dy)

    def map(self, point):
        return QtCore.QPointF(point.x() + self.dx, point.y() + self.dy)


@dataclass
class Painting:
    settings: dict
    points: tuple


class Layer:
    def __init__(self, view, identifier="layer", native=True):
        self.view = view
        self.identifier = identifier
        self.painted = []
        self.failure = None
        if not native:
            self.paintPolylineStroke = None

    def uniqueId(self):
        return self.identifier

    def paintAbility(self):
        return "PAINT"

    def paintPolylineStroke(self, points):
        if self.failure:
            raise RuntimeError(self.failure)
        self.painted.append(Painting(self.view.snapshot(), tuple(points)))


class Canvas:
    def __init__(self, application):
        self.application = application
        self.events = []
        self.pressed = False
        self.focused = False
        self.first_pen_preset = None

    def window(self):
        return self.application.window

    def parentWidget(self):
        return None

    def metaObject(self):
        return SimpleNamespace(className=lambda: "KisOpenGLCanvas2")

    def isVisible(self):
        return True

    def isEnabled(self):
        return True

    def setFocus(self, *args):
        self.focused = True

    def receive(self, event):
        self.events.append(event)
        if event == "hover" and self.first_pen_preset is not None:
            self.application.view.setCurrentBrushPreset(self.first_pen_preset)
            self.first_pen_preset = None
        if isinstance(event, tuple):
            phase, position, sample, timestamp = event
            if phase is InputPhase.PRESS:
                self.pressed = True
                self.settings = self.application.view.snapshot()
                self.points = []
            if phase is not InputPhase.RELEASE:
                self.points.append((position, sample, timestamp))
            else:
                self.pressed = False
                self.document.layer.painted.append(
                    Painting(self.settings, tuple(self.points))
                )


class Document:
    def __init__(self, application, path, identifier, native):
        self.path = path
        self.layer = Layer(application.view, native=native)
        self.root = Layer(application.view, identifier)
        self.canvas = Canvas(application)
        self.canvas.document = self

    def activeNode(self):
        return self.layer

    def rootNode(self):
        return self.root

    def fileName(self):
        return self.path or ""

    def tryBarrierLock(self):
        return True

    def unlock(self):
        pass

    def findChildren(self, widget_type):
        return [self.canvas]


class Krita:
    def __init__(self, document="/art/cat.kra", native=True):
        self.presets = Resources()
        self.view = View(self.presets)
        self.pressure_curve = "0,0;1,1;"
        self.pen_pressure_enabled = True
        self.actions = {
            name: SimpleNamespace(triggered=Signal()) for name in ("edit_undo", "edit_redo")
        }
        self.notifications = SimpleNamespace(imageSaved=Signal(), setActive=lambda value: None)
        self.window = SimpleNamespace(
            activeView=lambda: self.view,
            qwindow=lambda: self.window,
            findChild=lambda kind: SimpleNamespace(activeSubWindow=lambda: self.document),
        )
        self.open_documents = []
        self.document = self.add_document(document, "document", native)

    def add_document(self, path, identifier="other", native=True):
        document = Document(self, path, identifier, native)
        self.open_documents.append(document)
        return document

    def activeDocument(self):
        return self.document

    def activeWindow(self):
        return self.window

    def documents(self):
        return self.open_documents

    def notifier(self):
        return self.notifications

    def action(self, name):
        if name == "disable_pressure":
            return SimpleNamespace(isChecked=lambda: self.pen_pressure_enabled)
        return self.actions.get(name)

    def readSetting(self, group, key, default):
        return self.pressure_curve if key == "tabletPressureCurve" else default

    def resources(self, resource_type):
        reported = {"preset": "paintoppresets", "pattern": "patterns", "gradient": "gradients"}
        return {r.name(): r for r in self.presets.all() if r.type() == reported[resource_type]}


class MouseEvent:
    def __init__(self, kind, x, timestamp=0, shift=False):
        self.kind, self.x, self.timestamp_ms, self.shift = kind, x, timestamp, shift

    def type(self):
        return qt_enum(QtCore.QEvent, "Type", self.kind)

    def position(self):
        return QtCore.QPointF(self.x, 4)

    def timestamp(self):
        return self.timestamp_ms

    def button(self):
        return qt_enum(QtCore.Qt, "MouseButton", "LeftButton")

    def modifiers(self):
        modifier = "ShiftModifier" if self.shift else "NoModifier"
        return qt_enum(QtCore.Qt, "KeyboardModifier", modifier)


class TabletEvent(MouseEvent):
    def __init__(self, kind, x, timestamp=0, pressure=0.5, shift=False):
        super().__init__(kind, x, timestamp, shift)
        self.value = pressure

    def pressure(self):
        return self.value

    def xTilt(self):
        return -8

    def yTilt(self):
        return 14

    def rotation(self):
        return 35

    def tangentialPressure(self):
        return 0.6


class ReplayEvents:
    def enter(self, receiver, position):
        return "enter"

    def leave(self):
        return "leave"

    def hover(self, receiver, position, sample, timestamp_ms):
        return "hover"

    def create(self, phase, receiver, position, sample, timestamp_ms):
        return phase, position, sample, timestamp_ms


class EventLoop:
    def __init__(self):
        self.pending = deque()
        self.lock = Lock()

    def call_soon(self, callback):
        with self.lock:
            self.pending.append(callback)

    def call_later(self, delay_ms, callback):
        self.call_soon(callback)

    def post(self, receiver, event, on_delivered, on_error):
        def deliver():
            try:
                receiver.receive(event)
                on_delivered()
            except Exception as error:
                on_error(error)

        self.call_soon(deliver)

    def step(self):
        with self.lock:
            callback = self.pending.popleft()
        callback()

    def run(self):
        while self.pending:
            self.step()


class QtApplication:
    def __init__(self):
        self.filters = []

    def installEventFilter(self, event_filter):
        self.filters.append(event_filter)

    def removeEventFilter(self, event_filter):
        self.filters.remove(event_filter)

    def send(self, receiver, event):
        assert not any(f.eventFilter(receiver, event) for f in tuple(self.filters))

    def thread(self):
        return QtCore.QThread.currentThread()


class Plugin:

    def __init__(
        self, root: Path, qt, *, document="/art/cat.kra", native=True, worker=WORKER,
        library_worker=LIBRARY_WORKER,
    ):
        self.krita = Krita(document, native)
        self.loop = EventLoop()
        self.qt = qt
        self.panel = Panel()
        self.worker = worker
        self.library_worker = library_worker

        def replayer_factory(context, **options):
            return ObservedReplayer(KritaReplayer(
                context,
                scheduler=self.loop,
                stroke_point_factory=NativePoint,
                float_point_factory=lambda x, y: (x, y),
                pressure_mapper_provider=options["pressure_mapper_provider"],
                synthetic_input=SyntheticInputReplayer(
                    lambda: host.replay_input_target(), self.loop,
                    event_factory=ReplayEvents(), event_poster=self.loop,
                    report_issue=report_issue,
                ),
            ))

        host = KritaHost(self.krita, replayer_factory=replayer_factory, report_issue=report_issue)
        def service_factory(host, deliver, changed, library_changed):
            archive = ObservedArchive(RecordingArchive(root, deliver, worker, BUDGET, report_issue))
            library = RecordingBrowser(
                FileRecordingLibrary(root, library_worker, worker, BUDGET, deliver, report_issue),
                library_changed,
            )
            return BrushReplayService(host, RecordingWorkspace(archive), changed, library)

        self.runtime = BrushReplayRuntime(
            self.panel, service_factory=service_factory,
            host_factory=lambda _: host, input_filter_factory=ObservedInputFilter,
        )
        self.runtime.canvas_changed()

    @property
    def state(self):
        return self.panel.state

    @property
    def painted(self):
        return self.krita.document.layer.painted

    def start(self):
        self.panel.record_requested.emit()

    def stop(self):
        self.panel.stop_requested.emit()

    def input(self, kind, x=1, timestamp=0, *, tablet=False, **options):
        event = (TabletEvent if tablet else MouseEvent)(kind, x, timestamp, **options)
        self.qt.send(self.krita.document.canvas, event)

    def stroke(self, x=1, *, tablet=False, **options):
        names = ("TabletPress", "TabletMove", "TabletRelease") if tablet else (
            "MouseButtonPress", "MouseMove", "MouseButtonRelease"
        )
        for name, position, time in zip(names, (x, x + 1, x + 1), (10, 35, 40)):
            self.input(name, position, time, tablet=tablet, **options)

    def override(self, indexes, setting, value):
        self.choose_current()
        self.panel.override_requested.emit(indexes, setting, value)

    def reset(self, indexes, setting):
        self.choose_current()
        self.panel.reset_requested.emit(indexes, setting)

    def replay(self, *, current=False, wait=True):
        self.choose_current()
        self.painted.clear()
        signal = self.panel.replay_current_requested if current else self.panel.replay_requested
        signal.emit()
        if wait:
            self.loop.run()

    def choose_current(self):
        if self.state.source is None and self.state.current_recording is not None:
            self.panel.source_requested.emit(self.state.current_recording)
        elif self.state.source is None:
            recordings = self.library()
            key = next((row.key for row in recordings if row.key.document == self.state.document), None)
            if key is not None:
                self.choose(key)

    def library(self):
        self.panel.refresh_requested.emit()
        for _ in range(4):
            wait_for_writes(self.library_worker)
            self.qt.processEvents()
            if not self.panel.library_state.busy:
                return self.panel.library_state.recordings
        raise AssertionError("library did not become idle")

    def choose(self, key):
        self.panel.source_requested.emit(key)
        wait_for_writes(self.library_worker)
        self.qt.processEvents()

    def delete(self, *keys):
        self.panel.delete_requested.emit(tuple(keys))
        wait_for_writes(self.library_worker)
        self.qt.processEvents()

    def select(self, document):
        self.krita.document = document
        self.runtime.canvas_changed()

    def save(self, path):
        self.krita.document.path = path
        self.krita.notifications.imageSaved.emit(path)

    def saved(self):
        wait_for_writes(self.worker)
        self.qt.processEvents()

    def close(self):
        self.runtime.close()
        self.loop.run()


def wait_for_writes(worker=WORKER):
    completed = Event()
    worker.submit(object(), completed.set)
    assert completed.wait(5)


class PausedWrites:

    def __init__(self, worker=WORKER):
        self.held = {}
        self.paused = True
        self.worker = worker

    def submit(self, key, action):
        if self.paused:
            self.held[key] = action
        else:
            self.worker.submit(key, action)

    def wait_until_done(self, key, timeout_s):
        return key not in self.held and self.worker.wait_until_done(key, timeout_s)

    def resume(self):
        self.paused = False
        for key, action in self.held.items():
            self.worker.submit(key, action)
        self.held.clear()
