"""Non-consuming Qt event filter for mouse and tablet brush gestures."""

from __future__ import annotations

import logging
from collections.abc import Callable

from krita_brush_replay.domain.recording import (
    BrushSample,
    InputPhase,
    normalized_input_speed,
)
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, qt_enum

SampleHandler = Callable[[InputPhase, BrushSample | None], None]
ErrorHandler = Callable[[Exception], None]
logger = logging.getLogger(__name__)


def _event_type(name: str):
    return qt_enum(QtCore.QEvent, "Type", name)


_PHASE_BY_EVENT_TYPE = {
    _event_type("TabletPress"): InputPhase.PRESS,
    _event_type("MouseButtonPress"): InputPhase.PRESS,
    _event_type("TabletMove"): InputPhase.MOVE,
    _event_type("MouseMove"): InputPhase.MOVE,
    _event_type("TabletRelease"): InputPhase.RELEASE,
    _event_type("MouseButtonRelease"): InputPhase.RELEASE,
}


class CanvasInputFilter(QtCore.QObject):
    """Observe canvas events without preventing Krita from painting them."""

    def __init__(
        self,
        host,
        handler: SampleHandler,
        error_handler: ErrorHandler,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._handler = handler
        self._error_handler = error_handler
        self._target = None
        self._device_kind: str | None = None
        self._to_image = None
        self._last_position: tuple[float, float] | None = None
        self._last_timestamp_ms: int | None = None

    def eventFilter(self, watched, event) -> bool:
        try:
            self._dispatch(watched, event)
        except Exception as error:
            logger.exception("brush input capture failed")
            self._reset_stroke()
            try:
                self._error_handler(error)
            except Exception:
                logger.exception("brush input error handler failed")
        return False

    def _dispatch(self, watched, event) -> None:
        if isinstance(event, QtGui.QTabletEvent):
            self._handle(event, watched, "tablet")
        elif isinstance(event, QtGui.QMouseEvent) and not _is_synthesized_mouse(event):
            self._handle(event, watched, "mouse")

    def _handle(self, event, watched, device_kind: str) -> None:
        phase = _input_phase(event)
        if phase is None:
            return
        if phase is InputPhase.PRESS:
            if not _is_left_press(event) or not self._host.is_canvas_target(watched):
                return
            self._target = watched
            self._device_kind = device_kind
            self._to_image = self._host.capture_mapper()
            self._last_position = None
            self._last_timestamp_ms = None
        elif watched is not self._target or device_kind != self._device_kind:
            return

        if phase is InputPhase.RELEASE:
            self._handler(phase, None)
            self._reset_stroke()
            return

        position = _event_xy(event)
        timestamp_ms = max(0, int(_event_value(event, "timestamp", 0)))
        speed = normalized_input_speed(
            self._last_position,
            self._last_timestamp_ms,
            position,
            timestamp_ms,
        )
        if self._to_image is None:
            raise RuntimeError("the stroke has no captured coordinate mapper")
        x, y = self._to_image(position)
        sample = BrushSample(
            x=x,
            y=y,
            pressure=_pressure(event, device_kind),
            timestamp_ms=timestamp_ms,
            x_tilt=float(_event_value(event, "xTilt", 0.0)),
            y_tilt=float(_event_value(event, "yTilt", 0.0)),
            rotation=float(_event_value(event, "rotation", 0.0)),
            tangential_pressure=float(
                _event_value(event, "tangentialPressure", 0.0)
            ),
            speed=speed,
        )
        self._handler(phase, sample)
        self._last_position = position
        self._last_timestamp_ms = timestamp_ms

    def _reset_stroke(self) -> None:
        self._target = None
        self._device_kind = None
        self._to_image = None
        self._last_position = None
        self._last_timestamp_ms = None


def _input_phase(event) -> InputPhase | None:
    return _PHASE_BY_EVENT_TYPE.get(event.type())


def _left_button():
    return qt_enum(QtCore.Qt, "MouseButton", "LeftButton")


def _is_left_press(event) -> bool:
    button = getattr(event, "button", lambda: None)()
    return button == _left_button()


def _pressure(event, device_kind: str) -> float:
    if device_kind == "mouse":
        return 1.0
    value = getattr(event, "pressure", lambda: 1.0)()
    return min(1.0, max(0.0, float(value)))


def _is_synthesized_mouse(event) -> bool:
    source_getter = getattr(event, "source", None)
    if not callable(source_getter):
        return False
    sources = getattr(QtCore.Qt, "MouseEventSource", QtCore.Qt)
    not_synthesized = getattr(sources, "MouseEventNotSynthesized", None)
    return not_synthesized is not None and source_getter() != not_synthesized


def _event_xy(event) -> tuple[float, float]:
    for name in ("position", "localPos", "posF", "pos"):
        getter = getattr(event, name, None)
        if callable(getter):
            point = getter()
            return float(point.x()), float(point.y())
    raise RuntimeError("input event has no position")


def _event_value(event, name: str, default):
    getter = getattr(event, name, None)
    return getter() if callable(getter) else default
