from __future__ import annotations

from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum


def event_type(name: str):
    return qt_enum(QtCore.QEvent, "Type", name)


def event_value(event, name: str, default):
    getter = getattr(event, name, None)
    return getter() if callable(getter) else default


def left_button():
    return qt_enum(QtCore.Qt, "MouseButton", "LeftButton")


def is_left_button(event) -> bool:
    return event_value(event, "button", None) == left_button()


def is_left_held(event) -> bool:
    buttons = event_value(event, "buttons", None)
    return buttons is None or bool(buttons & left_button())


def is_brush_resize_gesture(event) -> bool:
    modifiers = event_value(event, "modifiers", None)
    shift = qt_enum(QtCore.Qt, "KeyboardModifier", "ShiftModifier")
    return modifiers is not None and bool(modifiers & shift)
