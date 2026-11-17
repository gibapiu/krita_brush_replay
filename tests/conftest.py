
from __future__ import annotations

import logging
import os
import sys
import tempfile
from importlib import import_module
from importlib.util import find_spec
from threading import Condition
from types import ModuleType, SimpleNamespace

import pytest

if find_spec("PyQt6") is not None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import_module("PyQt6.QtWidgets")

if "PyQt5" not in sys.modules and "PyQt6" not in sys.modules:
    pyqt6 = ModuleType("PyQt6")
    qt_core = ModuleType("PyQt6.QtCore")
    qt_gui = ModuleType("PyQt6.QtGui")
    qt_widgets = ModuleType("PyQt6.QtWidgets")

    class QObject:
        def __init__(self, parent=None) -> None:
            self.parent = parent

    class QEvent:
        Type = SimpleNamespace(
            TabletPress=1,
            MouseButtonPress=2,
            TabletMove=3,
            MouseMove=4,
            TabletRelease=5,
            MouseButtonRelease=6,
        )

    class QPointF:
        def __init__(self, x: float, y: float) -> None:
            self._x = x
            self._y = y

        def x(self) -> float:
            return self._x

        def y(self) -> float:
            return self._y

    class QApplication:
        @staticmethod
        def instance():
            return None

    class _BoundSignal:
        def __init__(self) -> None:
            self._slots = []

        def connect(self, slot, *connection_type) -> None:
            self._slots.append((slot, bool(connection_type)))

        def emit(self, *args) -> None:
            for slot, queued in tuple(self._slots):
                if queued:
                    qt_core.QTimer.singleShot(0, lambda slot=slot: slot(*args))
                else:
                    slot(*args)

    class pyqtSignal:
        def __init__(self, *types) -> None:
            self._name = ""

        def __set_name__(self, owner, name) -> None:
            self._name = f"_signal_{name}"

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.setdefault(self._name, _BoundSignal())

    class QStandardPaths:
        StandardLocation = SimpleNamespace(AppDataLocation=4)

        @staticmethod
        def writableLocation(location) -> str:
            return tempfile.gettempdir()

    qt_core.QObject = QObject
    qt_core.QStandardPaths = QStandardPaths
    qt_core.pyqtSignal = pyqtSignal
    qt_core.QEvent = QEvent
    qt_core.QPointF = QPointF
    qt_core.Qt = SimpleNamespace(
        MouseButton=SimpleNamespace(NoButton=0, LeftButton=1),
        KeyboardModifier=SimpleNamespace(NoModifier=0, ShiftModifier=0x02000000),
        MouseEventSource=SimpleNamespace(MouseEventNotSynthesized=0),
        ConnectionType=SimpleNamespace(QueuedConnection=2),
    )
    qt_gui.QTabletEvent = type("QTabletEvent", (), {})
    qt_gui.QMouseEvent = type("QMouseEvent", (), {})
    qt_widgets.QApplication = QApplication
    qt_widgets.QWidget = type("QWidget", (), {})
    qt_widgets.QMdiArea = type("QMdiArea", (), {})

    pyqt6.QtCore = qt_core
    pyqt6.QtGui = qt_gui
    pyqt6.QtWidgets = qt_widgets
    sys.modules.update(
        {
            "PyQt6": pyqt6,
            "PyQt6.QtCore": qt_core,
            "PyQt6.QtGui": qt_gui,
            "PyQt6.QtWidgets": qt_widgets,
        }
    )


def install_host_ports(monkeypatch):
    from support import (
        EventLoop,
        LinearCurve,
        ManagedColor,
        MouseEvent,
        QtApplication,
        TabletEvent,
    )

    from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtGui, QtWidgets

    real_application = None
    if hasattr(QtWidgets, "QListWidget"):
        real_application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ui_loop = EventLoop()
    qt = QtApplication()
    qt.warnings = []

    def process_events():
        if real_application is not None:
            real_application.processEvents()
        ui_loop.run()

    qt.processEvents = process_events
    module = ModuleType("krita")
    module.ManagedColor = ManagedColor
    module.KisCubicCurve = LinearCurve
    monkeypatch.setitem(sys.modules, "krita", module)
    monkeypatch.setattr(QtGui, "QTabletEvent", TabletEvent)
    monkeypatch.setattr(QtGui, "QMouseEvent", MouseEvent)
    monkeypatch.setattr(QtWidgets, "QApplication", SimpleNamespace(instance=lambda: qt))
    monkeypatch.setattr(
        QtWidgets,
        "QMessageBox",
        SimpleNamespace(warning=lambda parent, title, message: qt.warnings.append(message)),
        raising=False,
    )
    monkeypatch.setattr(
        QtCore,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: ui_loop.call_soon(callback)),
        raising=False,
    )
    monkeypatch.setattr(
        QtCore, "QCoreApplication", SimpleNamespace(instance=lambda: qt), raising=False,
    )
    if not hasattr(QtCore, "QThread"):
        monkeypatch.setattr(
            QtCore, "QThread", SimpleNamespace(currentThread=lambda: 1), raising=False
        )
    return qt


@pytest.fixture
def plugin_factory(tmp_path, monkeypatch):
    from support import LIBRARY_WORKER, Plugin, wait_for_writes

    qt = install_host_ports(monkeypatch)
    plugins = []

    def open_plugin(**options):
        plugin = Plugin(options.pop("root", tmp_path), qt, **options)
        plugins.append(plugin)
        return plugin

    yield open_plugin
    for plugin in plugins:
        plugin.close()
    wait_for_writes()
    wait_for_writes(LIBRARY_WORKER)
    wait_for_writes()
    qt.processEvents()


@pytest.fixture
def issue_log(tmp_path):
    output = SimpleNamespace(records=[])
    changed = Condition()

    def wait_for(predicate=lambda record: True):
        with changed:
            return changed.wait_for(lambda: any(predicate(r) for r in output.records), 5)

    output.wait_for = wait_for

    class Output(logging.Handler):
        def emit(self, record):
            text = record.getMessage() + (record.exc_text or "")
            if str(tmp_path) in text:
                with changed:
                    output.records.append(record)
                    changed.notify_all()

    logger = logging.getLogger("krita_brush_replay")
    previous = logger.level
    handler = Output()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    yield output
    logger.removeHandler(handler)
    logger.setLevel(previous)
