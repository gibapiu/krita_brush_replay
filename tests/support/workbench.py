"""User-facing interactions shared by composed Qt scenarios."""

from time import monotonic

from krita_brush_replay.composition import compose_runtime
from krita_brush_replay.infrastructure.krita_brush_state import KritaColorCodec
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets, qt_enum
from krita_brush_replay.ui.dock import BrushReplayPanel
from tests.support.krita import QtTest, mouse


def wait_until(predicate, timeout=2.0):
    deadline = monotonic() + timeout
    while not predicate():
        QtWidgets.QApplication.processEvents()
        if monotonic() >= deadline:
            raise AssertionError("Timed out waiting for an observable Qt result")


class Workbench:
    def __init__(self, krita):
        self.krita = krita
        self.panel = BrushReplayPanel(KritaColorCodec())
        self.dock = QtWidgets.QDockWidget(krita.window)
        self.dock.setWidget(self.panel)
        krita.window.addDockWidget(
            qt_enum(QtCore.Qt, "DockWidgetArea", "RightDockWidgetArea"), self.dock
        )
        self.runtime = compose_runtime(self.panel, krita)
        self.panel.show()

    def button(self, title):
        return next(
            button
            for button in self.panel.findChildren(QtWidgets.QPushButton)
            if button.text() == title
        )

    def click(self, title):
        QtTest.QTest.mouseClick(self.button(title), qt_enum(QtCore.Qt, "MouseButton", "LeftButton"))

    def field(self, title):
        return next(
            widget
            for widget in self.panel.findChildren(QtWidgets.QWidget)
            if widget.accessibleName() == title
        )

    def number(self, title, text):
        field = self.field(title)
        field.setFocus()
        field.selectAll()
        QtTest.QTest.keyClicks(field, str(text))
        QtTest.QTest.keyClick(field, qt_enum(QtCore.Qt, "Key", "Key_Return"))

    def choose(self, title, text):
        field = self.field(title)
        field.setFocus()
        QtTest.QTest.keyClick(field, qt_enum(QtCore.Qt, "Key", "Key_Home"))
        for _ in range(field.count()):
            if field.currentText() == text:
                return
            QtTest.QTest.keyClick(field, qt_enum(QtCore.Qt, "Key", "Key_Down"))
        raise AssertionError(f"Choice {text} was not selectable in {title}")

    def stroke(self, x=30):
        mouse(self.krita.canvas, "ButtonPress", x, timestamp=100)
        mouse(self.krita.canvas, "Move", x + 30, timestamp=110)
        mouse(self.krita.canvas, "ButtonRelease", x + 30, timestamp=120)

    def resize_brush_during_drag(self, size, *, x):
        mouse(self.krita.canvas, "ButtonPress", x)
        self.krita.view.setBrushSize(size)
        mouse(self.krita.canvas, "ButtonRelease", x + 10)

    def record(self, count=1):
        self.click("Record new session")
        for index in range(count):
            self.stroke(30 + index * 50)
        self.click("Stop")

    def replay(self, *, compare=False):
        self.click("Compare with current settings" if compare else "Replay")
        wait_until(lambda: self.button("Record new session").isEnabled())

    def message_contains(self, text):
        return any(
            text.lower() in label.text().lower()
            for label in self.panel.findChildren(QtWidgets.QLabel)
        )
