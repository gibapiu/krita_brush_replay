"""Non-consuming Qt event filter for mouse and tablet brush gestures."""

from __future__ import annotations

import logging
from collections.abc import Callable

from krita_brush_replay.domain.recording import (
    BrushSample,
    InputPhase,
    normalized_input_speed,
)
from krita_brush_replay.infrastructure.input_events import (
    event_device,
    event_phase,
    event_value,
    is_left_press,
    is_synthesized_mouse,
)
from krita_brush_replay.infrastructure.qt_facade import QtCore

SampleHandler = Callable[[InputPhase, BrushSample | None], None]
ErrorHandler = Callable[[Exception], None]
logger = logging.getLogger(__name__)


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
            self.reset()
            try:
                self._error_handler(error)
            except Exception:
                logger.exception("brush input error handler failed")
        return False

    def _dispatch(self, watched, event) -> None:
        device = event_device(event)
        if device is None or (device == "mouse" and is_synthesized_mouse(event)):
            return
        self._handle(event, watched, device)

    def _handle(self, event, watched, device_kind: str) -> None:
        phase = event_phase(event)
        if phase is None:
            return
        if device_kind == "mouse" and self._device_kind == "tablet":
            return
        if phase is InputPhase.PRESS:
            if not is_left_press(event) or not self._host.is_canvas_target(watched):
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
            self.reset()
            return

        position = _event_xy(event)
        timestamp_ms = max(0, int(event_value(event, "timestamp", 0)))
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
            x_tilt=float(event_value(event, "xTilt", 0.0)),
            y_tilt=float(event_value(event, "yTilt", 0.0)),
            rotation=float(event_value(event, "rotation", 0.0)),
            tangential_pressure=float(event_value(event, "tangentialPressure", 0.0)),
            speed=speed,
        )
        self._last_position = position
        self._last_timestamp_ms = timestamp_ms
        self._handler(phase, sample)

    def reset(self) -> None:
        self._target = None
        self._device_kind = None
        self._to_image = None
        self._last_position = None
        self._last_timestamp_ms = None


def _pressure(event, device_kind: str) -> float:
    if device_kind == "mouse":
        return 1.0
    value = getattr(event, "pressure", lambda: 1.0)()
    return min(1.0, max(0.0, float(value)))


def _event_xy(event) -> tuple[float, float]:
    for name in ("position", "localPos", "posF", "pos"):
        getter = getattr(event, name, None)
        if callable(getter):
            point = getter()
            return float(point.x()), float(point.y())
    raise RuntimeError("input event has no position")
