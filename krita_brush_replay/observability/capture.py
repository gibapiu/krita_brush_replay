from __future__ import annotations

from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.input_events import (
    event_phase,
    event_value,
    is_synthesized_mouse,
)
from krita_brush_replay.infrastructure.input_filter import CanvasInputFilter
from krita_brush_replay.infrastructure.qt_facade import QtGui
from krita_brush_replay.observability.monitor import Monitor
from krita_brush_replay.observability.pressure import PressureTrace


class ObservedInputFilter(CanvasInputFilter):
    def __init__(
        self, host, handler, error_handler, parent=None, *, monitor: Monitor, brush_snapshot=None
    ) -> None:
        self._monitor = monitor
        self._brush_snapshot = brush_snapshot
        self._accept = handler
        self._fail = error_handler
        self._event = None
        self._accepted = False
        self._capture_target = None
        self._capture_device = None
        self._stroke_number = 0
        self._trace = PressureTrace()
        self._ignored: dict[str, int] = {}
        self._ignored_count = 0
        super().__init__(host, self._sample, self._error, parent)

    def eventFilter(self, watched, event) -> bool:
        if not self._monitor.enabled:
            return super().eventFilter(watched, event)
        previous = self._event, self._accepted
        self._event = (watched, event)
        self._accepted = False
        try:
            return super().eventFilter(watched, event)
        finally:
            self._monitor.observe(self._ignored_event)
            self._event, self._accepted = previous

    def _sample(self, phase, sample) -> None:
        if self._monitor.enabled:
            self._monitor.observe(self._record_sample, phase, sample)
            if phase is InputPhase.PRESS and self._brush_snapshot is not None:
                self._monitor.observe(self._record_brush)
        self._accept(phase, sample)

    def _error(self, error) -> None:
        if self._monitor.enabled:
            self._monitor.observe(self._monitor.log, "capture-error error=%s", error)
        self._fail(error)

    def _record_sample(self, phase, sample) -> None:
        self._accepted = True
        if self._event is None:
            return
        watched, event = self._event
        if phase is InputPhase.PRESS:
            self._finish("replaced-by-press")
            self._stroke_number += 1
            self._capture_target = watched
            self._capture_device = "tablet" if isinstance(event, QtGui.QTabletEvent) else "mouse"
            self._monitor.log(
                "capture-begin stroke=%s device=%s timestamp_ms=%s receiver=%s",
                self._stroke_number,
                self._capture_device,
                event_value(event, "timestamp", 0),
                type(watched).__name__,
            )
        if sample is not None:
            self._trace.observe(sample)
        if phase is InputPhase.RELEASE:
            self._monitor.log(
                "capture-release stroke=%s raw_pressure=%s",
                self._stroke_number,
                event_value(event, "pressure", None),
            )
            self._finish("release")

    def _ignored_event(self) -> None:
        if self._accepted or self._event is None:
            return
        watched, event = self._event
        phase = event_phase(event)
        if watched is not self._capture_target or phase is None:
            return
        if isinstance(event, QtGui.QMouseEvent):
            device = "mouse"
            reason = "not-accepted"
            if is_synthesized_mouse(event):
                reason = "qt-synthesized"
            elif self._capture_device == "tablet":
                reason = "tablet-active"
        elif isinstance(event, QtGui.QTabletEvent):
            device, reason = "tablet", "not-accepted"
        else:
            return
        key = f"{device}:{phase.name}:{reason}:source={event_value(event, 'source', None)}"
        self._ignored[key] = self._ignored.get(key, 0) + 1
        self._ignored_count += 1
        if self._ignored_count <= 4:
            self._monitor.log(
                "capture-ignore stroke=%s event=%s timestamp_ms=%s",
                self._stroke_number,
                key,
                event_value(event, "timestamp", 0),
            )

    def _finish(self, reason: str) -> None:
        if self._capture_device is not None:
            self._monitor.log(
                "capture-end stroke=%s device=%s reason=%s %s ignored_events=%s",
                self._stroke_number,
                self._capture_device,
                reason,
                self._trace,
                self._ignored,
            )
        self._capture_target = None
        self._capture_device = None
        self._trace = PressureTrace()
        self._ignored = {}
        self._ignored_count = 0

    def reset(self) -> None:
        if self._monitor.enabled:
            self._monitor.observe(self._finish, "reset")
        super().reset()

    def _record_brush(self) -> None:
        self._monitor.log("capture-brush %s", self._brush_snapshot())
