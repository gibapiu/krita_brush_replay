"""A small external Krita protocol double; plugin components remain real.

Paint observations come from the external node/canvas, not the plugin's own capture
adapter. This fixture does not claim to simulate Krita's brush engines or pixels.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets, qt_enum

QtTest = __import__(f"{QtCore.__package__}.QtTest", fromlist=["QTest"])


@dataclass
class Resource:
    kind: str
    title: str
    path: str
    size: float = 12.345
    accepts_size: bool = True

    def type(self):
        return self.kind

    def name(self):
        return self.title

    def filename(self):
        return self.path


class ManagedColor:
    def __init__(self, model="RGBA", depth="U8", profile="sRGB") -> None:
        self.space = model, depth, profile
        self.values = [0.1, 0.2, 0.3, 1.0]

    def colorModel(self):
        return self.space[0]

    def colorDepth(self):
        return self.space[1]

    def colorProfile(self):
        return self.space[2]

    def components(self):
        return list(self.values)

    def setComponents(self, values):
        self.values = list(values)
        return True

    def colorForCanvas(self, canvas):
        return QtGui.QColor.fromRgbF(*self.values)

    @classmethod
    def fromQColor(cls, color):
        result = cls()
        result.values = [color.redF(), color.greenF(), color.blueF(), color.alphaF()]
        return result


class View:
    def __init__(self, application) -> None:
        self.application = application
        self.preset = application.ink
        self.size = self.preset.size
        self.opacity = 0.8
        self.flow = 0.6
        self.rotation = 32.0
        self.foreground = ManagedColor()
        self.background = ManagedColor()
        self.background.values = [0.8, 0.7, 0.6, 1.0]
        self.pattern = application.paper
        self.gradient = application.ramp
        self.blending = "multiply"
        self.transform = QtGui.QTransform()
        self.reject_blending = False

    def currentBrushPreset(self):
        if self.application.capture_error:
            raise RuntimeError(self.application.capture_error)
        return self.preset

    def setCurrentBrushPreset(self, resource):
        self.preset = resource
        self.size = resource.size

    def brushSize(self):
        return self.size

    def setBrushSize(self, value):
        if self.preset.accepts_size:
            self.size = self.preset.size = value

    def paintingOpacity(self):
        return self.opacity

    def setPaintingOpacity(self, value):
        self.opacity = value

    def paintingFlow(self):
        return self.flow

    def setPaintingFlow(self, value):
        self.flow = value

    def brushRotation(self):
        return self.rotation

    def setBrushRotation(self, value):
        self.rotation = value

    def foregroundColor(self):
        return self.foreground

    def setForeGroundColor(self, value):
        self.foreground = value

    def backgroundColor(self):
        return self.background

    def setBackGroundColor(self, value):
        self.background = value

    def currentPattern(self):
        return self.pattern

    def setCurrentPattern(self, value):
        self.pattern = value

    def currentGradient(self):
        return self.gradient

    def setCurrentGradient(self, value):
        self.gradient = value

    def currentBlendingMode(self):
        return self.blending

    def setCurrentBlendingMode(self, value):
        if not self.reject_blending:
            self.blending = value

    def flakeToCanvasTransform(self):
        return self.transform

    def flakeToImageTransform(self):
        return QtGui.QTransform()

    def snapshot(self):
        return {
            "preset": self.preset.filename(),
            "size": self.size,
            "opacity": self.opacity,
            "flow": self.flow,
            "rotation": self.rotation,
            "foreground": tuple(self.foreground.values),
            "background": tuple(self.background.values),
            "pattern": self.pattern.filename(),
            "gradient": self.gradient.filename(),
            "blending_mode": self.blending,
        }


class ViewHandle:
    """Krita can return a new Python wrapper for the same underlying view."""

    def __init__(self, view):
        self.view = view

    def __getattr__(self, name):
        return getattr(self.view, name)


@dataclass
class StrokePoint:
    position: object
    pressure: float
    x_tilt: float
    y_tilt: float
    rotation: float
    tangential_pressure: float
    time: float
    speed: float


class Layer:
    def __init__(self, application, identifier, *, native=True) -> None:
        self.application = application
        self.identifier = identifier
        self.writes = []
        self.failure = None
        self.writable = True
        if not native:
            self.paintPolylineStroke = None

    def uniqueId(self):
        return self.identifier

    def paintAbility(self):
        return "PAINT" if self.writable else "NONE"

    def paintPolylineStroke(self, points):
        if self.failure:
            raise RuntimeError(self.failure)
        self.writes.append((self.application.view.snapshot(), tuple(points)))
        if self.application.after_paint:
            self.application.after_paint()


class Document:
    def __init__(self, application) -> None:
        self.root = Layer(application, "document")
        self.layer = Layer(application, "recorded-layer")
        self.busy = False
        self.completion_checked = False

    def rootNode(self):
        return self.root

    def activeNode(self):
        return self.layer

    def tryBarrierLock(self):
        self.completion_checked = True
        return not self.busy

    def unlock(self):
        pass


class Canvas(QtWidgets.QWidget):
    def __init__(self, application, parent) -> None:
        super().__init__(parent)
        self.application = application
        self.setObjectName("canvas")
        self.setAttribute(qt_enum(QtCore.Qt, "WidgetAttribute", "WA_TabletTracking"), True)
        self.setFocusPolicy(qt_enum(QtCore.Qt, "FocusPolicy", "StrongFocus"))
        self.received = []
        self.gestures = []
        self.active = None
        self.on_press = None
        self.on_move = None

    def event(self, event):
        kind = event.type()
        if kind == qt_enum(QtCore.QEvent, "Type", "Leave"):
            self.received.append("leave")
        elif kind == qt_enum(QtCore.QEvent, "Type", "Enter"):
            self.received.append("enter")
        elif isinstance(event, QtGui.QTabletEvent):
            phase = {
                qt_enum(QtCore.QEvent, "Type", "TabletPress"): "press",
                qt_enum(QtCore.QEvent, "Type", "TabletMove"): "move",
                qt_enum(QtCore.QEvent, "Type", "TabletRelease"): "release",
            }.get(kind)
            if phase:
                position = event.position() if hasattr(event, "position") else event.posF()
                observation = (
                    phase,
                    position.x(),
                    position.y(),
                    event.pressure(),
                    event.xTilt(),
                    event.yTilt(),
                    event.rotation(),
                    event.tangentialPressure(),
                )
                self.received.append(observation)
                if phase == "press":
                    self.active = (self.application.view.snapshot(), [observation])
                    if self.on_press:
                        self.on_press()
                elif self.active is not None:
                    self.active[1].append(observation)
                    if phase == "move" and self.on_move:
                        self.on_move()
                    if phase == "release":
                        self.gestures.append(self.active)
                        self.active = None
                elif self.application.pen_initialization:
                    self.application.view.setCurrentBrushPreset(self.application.pencil)
                    self.application.pen_initialization = False
                event.accept()
                return True
        return super().event(event)


class Curve:
    """Literal responses from an external curve service, not an interpolation implementation."""

    def __init__(self, text):
        self.text = text

    def floatTransfer(self, size):
        return self.text

    @staticmethod
    def interpolateLinear(pressure, transfer):
        examples = {
            ("0,0;0.5,0.8;1,1;", 0.5): 0.8,
            ("0,0;0.5,0.8;1,1;", 0.8): 0.92,
            ("0,0;0.5,0.3;1,1;", 0.5): 0.3,
            ("0,0;0.5,0.3;1,1;", 0.3): 0.18,
        }
        return examples.get((transfer, pressure), pressure)


class Krita:
    def __init__(self, monkeypatch) -> None:
        self.ink = Resource("paintoppresets", "Ink", "ink.kpp")
        self.pencil = Resource("paintoppresets", "Pencil", "pencil.kpp", 3.0)
        self.fixed = Resource("paintoppresets", "Fixed", "fixed.kpp", 1.0, False)
        self.paper = Resource("patterns", "Paper", "paper.pat")
        self.canvas_pattern = Resource("patterns", "Canvas", "canvas.pat")
        self.ramp = Resource("gradients", "Ramp", "ramp.svg")
        self.mesh = Resource("gradients", "Mesh", "mesh.svg")
        self.available = [
            self.ink,
            self.pencil,
            self.fixed,
            self.paper,
            self.canvas_pattern,
            self.ramp,
            self.mesh,
        ]
        self.capture_error = None
        self.resource_error = None
        self.after_paint = None
        self.pen_initialization = False
        self.pressure_enabled = True
        self.pressure_available = True
        self.curve = "0,0;1,1;"
        self.view = View(self)
        self.document = Document(self)
        self.window = QtWidgets.QMainWindow()
        self.canvas = Canvas(self, self.window)
        self.window.setCentralWidget(self.canvas)
        self.window.resize(500, 300)
        self.window.show()
        self.actions = {
            name: QtGui.QAction(name, self.window)
            if hasattr(QtGui, "QAction")
            else QtWidgets.QAction(name, self.window)
            for name in ("edit_undo", "edit_redo")
        }
        self.factories = []
        module = ModuleType("krita")
        module.ManagedColor = ManagedColor
        module.StrokePoint = StrokePoint
        module.KisCubicCurve = Curve
        module.Krita = SimpleNamespace(instance=lambda: self)
        module.DockWidget = QtWidgets.QDockWidget
        module.DockWidgetFactory = lambda identifier, location, kind: SimpleNamespace(
            identifier=identifier, kind=kind
        )
        module.DockWidgetFactoryBase = SimpleNamespace(DockRight=1)
        monkeypatch.setitem(sys.modules, "krita", module)

    def activeDocument(self):
        return self.document

    def activeWindow(self):
        return self

    def activeView(self):
        return ViewHandle(self.view)

    def qwindow(self):
        return self.window

    def resources(self, kind):
        if self.resource_error:
            raise RuntimeError(self.resource_error)
        reported = {"preset": "paintoppresets", "pattern": "patterns", "gradient": "gradients"}
        return {item.name(): item for item in self.available if item.type() == reported[kind]}

    def readSetting(self, group, key, default):
        return self.curve if key == "tabletPressureCurve" else default

    def action(self, name):
        if name == "disable_pressure":
            return (
                SimpleNamespace(isChecked=lambda: self.pressure_enabled)
                if self.pressure_available
                else None
            )
        return self.actions.get(name)

    def addDockWidgetFactory(self, factory):
        self.factories.append(factory)


class TimedMouseEvent(QtGui.QMouseEvent):
    def timestamp(self):
        return self.input_timestamp


class TimedTabletEvent(QtGui.QTabletEvent):
    def timestamp(self):
        return self.input_timestamp


def mouse(canvas, phase, x=30, y=40, *, timestamp=100, shift=False, held=True):
    kind = qt_enum(QtCore.QEvent, "Type", f"Mouse{phase}")
    left = qt_enum(QtCore.Qt, "MouseButton", "LeftButton")
    none = qt_enum(QtCore.Qt, "MouseButton", "NoButton")
    modifiers = qt_enum(QtCore.Qt, "KeyboardModifier", "ShiftModifier" if shift else "NoModifier")
    point = QtCore.QPointF(x, y)
    event = TimedMouseEvent(
        kind,
        point,
        QtCore.QPointF(canvas.mapToGlobal(point.toPoint())),
        none if phase == "Move" else left,
        none if phase == "ButtonRelease" or not held else left,
        modifiers,
    )
    event.input_timestamp = timestamp
    QtWidgets.QApplication.sendEvent(canvas, event)


def tablet(canvas, phase, x=30, y=40, *, pressure=0.5, timestamp=100, shift=False):
    kind = qt_enum(QtCore.QEvent, "Type", f"Tablet{phase}")
    left = qt_enum(QtCore.Qt, "MouseButton", "LeftButton")
    none = qt_enum(QtCore.Qt, "MouseButton", "NoButton")
    modifiers = qt_enum(QtCore.Qt, "KeyboardModifier", "ShiftModifier" if shift else "NoModifier")
    point = QtCore.QPointF(x, y)
    global_point = QtCore.QPointF(canvas.mapToGlobal(point.toPoint()))
    values = (
        point,
        global_point,
        pressure if phase != "Release" else 0.0,
        -8,
        14,
        0.6,
        35.0,
        0,
        modifiers,
    )
    button = none if phase == "Move" else left
    buttons = none if phase == "Release" else left
    if hasattr(QtGui, "QPointingDevice"):
        event = TimedTabletEvent(
            kind, QtGui.QPointingDevice.primaryPointingDevice(), *values, button, buttons
        )
    else:
        event = TimedTabletEvent(
            kind,
            point,
            global_point,
            qt_enum(QtGui.QTabletEvent, "TabletDevice", "Stylus"),
            qt_enum(QtGui.QTabletEvent, "PointerType", "Pen"),
            *values[2:],
            0,
            button,
            buttons,
        )
    event.input_timestamp = timestamp
    QtWidgets.QApplication.sendEvent(canvas, event)
