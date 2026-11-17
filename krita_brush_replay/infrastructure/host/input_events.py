from __future__ import annotations

from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtGui, qt_enum


def event_type(name: str):
    return qt_enum(QtCore.QEvent, "Type", name)


_PHASE_BY_EVENT_TYPE = {
    event_type("TabletPress"): InputPhase.PRESS,
    event_type("MouseButtonPress"): InputPhase.PRESS,
    event_type("TabletMove"): InputPhase.MOVE,
    event_type("MouseMove"): InputPhase.MOVE,
    event_type("TabletRelease"): InputPhase.RELEASE,
    event_type("MouseButtonRelease"): InputPhase.RELEASE,
}


def event_phase(event) -> InputPhase | None:
    return _PHASE_BY_EVENT_TYPE.get(event.type())


def event_device(event) -> str | None:
    if isinstance(event, QtGui.QTabletEvent):
        return "tablet"
    if isinstance(event, QtGui.QMouseEvent):
        return "mouse"
    return None


def event_value(event, name: str, default):
    getter = getattr(event, name, None)
    return getter() if callable(getter) else default


def left_button():
    return qt_enum(QtCore.Qt, "MouseButton", "LeftButton")


def is_left_press(event) -> bool:
    return event_value(event, "button", None) == left_button()


def is_left_held(event) -> bool:
    buttons = event_value(event, "buttons", None)
    return buttons is None or bool(buttons & left_button())


def is_brush_resize_gesture(event) -> bool:
    modifiers = event_value(event, "modifiers", None)
    shift = qt_enum(QtCore.Qt, "KeyboardModifier", "ShiftModifier")
    return modifiers is not None and bool(modifiers & shift)


def is_synthesized_mouse(event) -> bool:
    source = event_value(event, "source", None)
    if source is None:
        return False
    sources = getattr(QtCore.Qt, "MouseEventSource", QtCore.Qt)
    not_synthesized = getattr(sources, "MouseEventNotSynthesized", None)
    return not_synthesized is not None and source != not_synthesized
