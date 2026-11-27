"""Non-consuming Qt event filter that records the canvas strokes Krita paints."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from krita_brush_replay.domain.recording import (
    BrushSample,
    InputPhase,
    normalized_input_speed,
)
from krita_brush_replay.infrastructure.input_events import (
    event_type,
    event_value,
    is_brush_resize_gesture,
    is_left_button,
    is_left_held,
)
from krita_brush_replay.infrastructure.qt_facade import QtCore

SampleHandler = Callable[[InputPhase, BrushSample | None], None]
ErrorHandler = Callable[[Exception], None]
logger = logging.getLogger(__name__)

@dataclass(slots=True)
class _Stroke:
    target: object
    pen: bool
    to_image: Callable[[tuple[float, float]], tuple[float, float]]
    dropped_press: tuple | None = None
    left_canvas: bool = False
    last_position: tuple[float, float] | None = None
    last_timestamp_ms: int | None = None

    def sample(self, event) -> BrushSample:
        position = _event_xy(event)
        timestamp_ms = max(0, int(event_value(event, "timestamp", 0)))
        speed = normalized_input_speed(
            self.last_position,
            self.last_timestamp_ms,
            position,
            timestamp_ms,
        )
        self.last_position = position
        self.last_timestamp_ms = timestamp_ms
        x, y = self.to_image(position)
        return BrushSample(
            x=x,
            y=y,
            pressure=min(1.0, max(0.0, float(event.pressure()))) if self.pen else 1.0,
            timestamp_ms=timestamp_ms,
            x_tilt=float(event_value(event, "xTilt", 0.0)),
            y_tilt=float(event_value(event, "yTilt", 0.0)),
            rotation=float(event_value(event, "rotation", 0.0)),
            tangential_pressure=float(event_value(event, "tangentialPressure", 0.0)),
            speed=speed,
        )


class CanvasInputFilter(QtCore.QObject):
    """Record the strokes Krita paints, skipping input Krita ignores."""

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
        self._handlers = {
            event_type("TabletPress"): self._pen_press,
            event_type("TabletMove"): self._pen_move,
            event_type("TabletRelease"): self._pen_release,
            event_type("TabletLeaveProximity"): self._pen_left,
            event_type("MouseButtonPress"): self._mouse_press,
            event_type("MouseButtonDblClick"): self._mouse_press,
            event_type("MouseMove"): self._mouse_move,
            event_type("MouseButtonRelease"): self._mouse_release,
            event_type("Leave"): self._canvas_left,
            event_type("FocusOut"): self._canvas_lost,
            event_type("Hide"): self._canvas_lost,
        }
        self.reset()

    def eventFilter(self, watched, event) -> bool:
        handle = self._handlers.get(event.type())
        if handle is None:
            return False
        try:
            handle(watched, event)
        except Exception as error:
            logger.exception("brush input capture failed")
            self.reset()
            try:
                self._error_handler(error)
            except Exception:
                logger.exception("brush input error handler failed")
        return False

    def reset(self) -> None:
        self._stroke: _Stroke | None = None

    @property
    def _pen_down(self) -> bool:
        return self._stroke is not None and self._stroke.pen

    def _new_stroke(self, target, event, *, pen: bool) -> _Stroke | None:
        if (
            self._pen_down
            or not is_left_button(event)
            or is_brush_resize_gesture(event)
            or not self._host.is_canvas_target(target)
        ):
            return None
        return _Stroke(target, pen, self._host.capture_mapper(target))

    def _begin(self, stroke: _Stroke, sample: BrushSample) -> None:
        self._finish()
        self._stroke = stroke
        self._handler(InputPhase.PRESS, sample)

    def _finish(self) -> None:
        if self._stroke is not None:
            self._stroke = None
            self._handler(InputPhase.RELEASE, None)

    def _pen_press(self, target, event) -> None:
        stroke = self._new_stroke(target, event, pen=True)
        if stroke is not None:
            self._begin(stroke, stroke.sample(event))

    def _pen_move(self, target, event) -> None:
        if not self._pen_down:
            return
        if not is_left_held(event):
            self._finish()
            return
        self._stroke.left_canvas = False
        if target is self._stroke.target:
            self._handler(InputPhase.MOVE, self._stroke.sample(event))

    def _pen_release(self, target, event) -> None:
        if self._pen_down and (is_left_button(event) or not is_left_held(event)):
            self._finish()

    def _pen_left(self, target, event) -> None:
        if self._pen_down:
            self._finish()

    def _mouse_press(self, target, event) -> None:
        stroke = self._stroke
        if stroke is not None and stroke.dropped_press == (target, _press_identity(event)):
            self._stroke = None
            self._handler(InputPhase.CANCEL, None)
            return
        if self._pen_down and stroke.left_canvas:
            self._finish()
        stroke = self._new_stroke(target, event, pen=False)
        if stroke is not None:
            stroke.dropped_press = (target.parentWidget(), _press_identity(event))
            self._begin(stroke, stroke.sample(event))

    def _mouse_move(self, target, event) -> None:
        stroke = self._stroke
        if stroke is not None and not stroke.pen and target is stroke.target:
            self._handler(InputPhase.MOVE, stroke.sample(event))

    def _mouse_release(self, target, event) -> None:
        stroke = self._stroke
        if (
            stroke is not None
            and not stroke.pen
            and (is_left_button(event) or not is_left_held(event))
        ):
            self._finish()

    def _canvas_left(self, target, event) -> None:
        if self._pen_down and target is self._stroke.target:
            self._stroke.left_canvas = True

    def _canvas_lost(self, target, event) -> None:
        if self._stroke is not None and target is self._stroke.target:
            self._finish()


def _press_identity(event) -> tuple:
    point = event_value(event, "globalPosition", None)
    point = event.globalPos() if point is None else point.toPoint()
    return event.type(), event.button(), point.x(), point.y()


def _event_xy(event) -> tuple[float, float]:
    for name in ("position", "localPos", "posF", "pos"):
        getter = getattr(event, name, None)
        if callable(getter):
            point = getter()
            return float(point.x()), float(point.y())
    raise RuntimeError("input event has no position")
