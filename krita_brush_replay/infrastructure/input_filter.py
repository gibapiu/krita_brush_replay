"""Non-consuming Qt event filter for mouse and tablet brush gestures."""

from __future__ import annotations

import logging
from collections.abc import Callable

from krita_brush_replay.domain.recording import (
    BrushSample,
    InputPhase,
    normalized_input_speed,
)
from krita_brush_replay.infrastructure.diagnostics import PressureTrace
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
        self._stroke_number = 0
        self._pressure_trace = PressureTrace()
        self._ignored_events: dict[str, int] = {}
        self._ignored_event_count = 0

    def eventFilter(self, watched, event) -> bool:
        try:
            self._dispatch(watched, event)
        except Exception as error:
            logger.exception("brush input capture failed")
            self.finish_capture("capture-error")
            try:
                self._error_handler(error)
            except Exception:
                logger.exception("brush input error handler failed")
        return False

    def _dispatch(self, watched, event) -> None:
        if isinstance(event, QtGui.QTabletEvent):
            self._handle(event, watched, "tablet")
        elif isinstance(event, QtGui.QMouseEvent):
            if _is_synthesized_mouse(event):
                if watched is self._target:
                    self._log_ignored(event, "mouse", "qt-synthesized")
                return
            self._handle(event, watched, "mouse")

    def _handle(self, event, watched, device_kind: str) -> None:
        phase = _input_phase(event)
        if phase is None:
            return
        # OS-generated pen mouse events do not always carry Qt's synthetic flag.
        # Keep tablet ownership until its release, including across mouse presses.
        if device_kind == "mouse" and self._device_kind == "tablet":
            if watched is self._target:
                self._log_ignored(event, device_kind, "tablet-active")
            return
        if phase is InputPhase.PRESS:
            if not _is_left_press(event) or not self._host.is_canvas_target(watched):
                return
            self.finish_capture("replaced-by-press")
            self._target = watched
            self._device_kind = device_kind
            self._to_image = self._host.capture_mapper()
            self._last_position = None
            self._last_timestamp_ms = None
            self._stroke_number += 1
            logger.info(
                "capture-begin stroke=%s device=%s timestamp_ms=%s receiver=%s",
                self._stroke_number,
                device_kind,
                _event_value(event, "timestamp", 0),
                type(watched).__name__,
            )
        elif watched is not self._target or device_kind != self._device_kind:
            if watched is self._target:
                self._log_ignored(event, device_kind, "device-mismatch")
            return

        if phase is InputPhase.RELEASE:
            logger.info(
                "capture-release stroke=%s device=%s raw_pressure=%s timestamp_ms=%s",
                self._stroke_number,
                device_kind,
                _event_value(event, "pressure", None),
                _event_value(event, "timestamp", 0),
            )
            self.finish_capture("release")
            self._handler(phase, None)
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
            tangential_pressure=float(_event_value(event, "tangentialPressure", 0.0)),
            speed=speed,
        )
        self._pressure_trace.observe(sample)
        self._last_position = position
        self._last_timestamp_ms = timestamp_ms
        self._handler(phase, sample)

    def _log_ignored(self, event, device_kind: str, reason: str) -> None:
        phase = _input_phase(event)
        if phase is None:
            return
        source = _event_value(event, "source", None)
        key = f"{device_kind}:{phase.name}:{reason}:source={source}"
        self._ignored_events[key] = self._ignored_events.get(key, 0) + 1
        self._ignored_event_count += 1
        if self._ignored_event_count <= 4:
            logger.info(
                "capture-ignore stroke=%s event=%s timestamp_ms=%s raw_pressure=%s",
                self._stroke_number,
                key,
                _event_value(event, "timestamp", 0),
                _event_value(event, "pressure", None),
            )

    def finish_capture(self, reason: str = "recording-stopped") -> None:
        """Finish diagnostics and clear ownership when capture is detached."""
        if self._device_kind is not None:
            logger.info(
                "capture-end stroke=%s device=%s reason=%s %s ignored_events=%s",
                self._stroke_number,
                self._device_kind,
                reason,
                self._pressure_trace,
                self._ignored_events,
            )
        self._reset_stroke()

    def _reset_stroke(self) -> None:
        self._target = None
        self._device_kind = None
        self._to_image = None
        self._last_position = None
        self._last_timestamp_ms = None
        self._pressure_trace = PressureTrace()
        self._ignored_events = {}
        self._ignored_event_count = 0


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
