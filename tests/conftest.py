"""Minimal Qt modules for unit tests that run outside Krita."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

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

    qt_core.QObject = QObject
    qt_core.QEvent = QEvent
    qt_core.QPointF = QPointF
    qt_core.Qt = SimpleNamespace(
        MouseButton=SimpleNamespace(LeftButton=1),
        MouseEventSource=SimpleNamespace(MouseEventNotSynthesized=0),
    )
    qt_gui.QTabletEvent = type("QTabletEvent", (), {})
    qt_gui.QMouseEvent = type("QMouseEvent", (), {})
    qt_widgets.QApplication = QApplication

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
