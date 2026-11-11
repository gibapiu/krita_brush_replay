"""Queued tablet gestures for Krita versions without native stroke replay."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from krita_brush_replay.domain.recording import BrushSample, InputPhase
from krita_brush_replay.infrastructure.krita_replay import Scheduler
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CanvasInputTarget:
    """One canvas receiver with a stable image-to-widget mapping."""

    receiver: object
    to_widget: Callable[[tuple[float, float]], tuple[float, float]]
    is_valid: Callable[[], bool]
    focus: Callable[[], None]


def _require_gui_thread() -> None:
    application = QtCore.QCoreApplication.instance()
    if application is None or QtCore.QThread.currentThread() != application.thread():
        raise RuntimeError("canvas replay must run on Krita's GUI thread")


class SyntheticInputReplayer:
    """Replay on the GUI thread, yielding after every posted input event."""

    def __init__(
        self,
        target_provider: Callable[[], CanvasInputTarget],
        scheduler: Scheduler,
        *,
        event_factory: QtInputEventFactory | None = None,
        event_poster: QtEventPoster | None = None,
    ) -> None:
        self._target_provider = target_provider
        self._scheduler = scheduler
        self._event_factory = event_factory if event_factory is not None else QtInputEventFactory()
        self._event_poster = event_poster

    def prepare(self) -> CanvasInputTarget:
        _require_gui_thread()
        target = self._target_provider()
        if not target.is_valid():
            raise RuntimeError("the active canvas changed before replay")
        if self._event_poster is None:
            self._event_poster = QtEventPoster()
        return target

    def replay_stroke(
        self,
        target: CanvasInputTarget,
        samples: tuple[BrushSample, ...],
        require_context: Callable[[], None],
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        before_press: Callable[[], None] | None = None,
    ) -> None:
        _require_gui_thread()
        poster = self._event_poster
        if poster is None:
            raise RuntimeError("prepare the canvas before replay")
        factory = self._event_factory
        started_ms = int(monotonic() * 1000)
        source_ms = samples[0].timestamp_ms
        entered = False
        pressed = False
        finished = False
        index = 0

        def post(event, callback) -> None:
            poster.post(target.receiver, event, callback, finish)

        def finish(error: Exception | None = None) -> None:
            nonlocal finished, pressed, entered
            if finished:
                return
            finished = True

            def report() -> None:
                if error is None:
                    on_complete()
                else:
                    on_error(error)

            def cleanup_failed(cleanup_error: Exception) -> None:
                logger.debug("canvas input cleanup failed", exc_info=cleanup_error)
                # A deleted receiver cannot consume cleanup events.
                on_error(error if error is not None else cleanup_error)

            def leave() -> None:
                nonlocal entered
                try:
                    if entered:
                        entered = False
                        poster.post(target.receiver, factory.leave(), report, cleanup_failed)
                    else:
                        report()
                except Exception as cleanup_error:
                    cleanup_failed(cleanup_error)

            try:
                if pressed:
                    pressed = False
                    # Release the original receiver even if the active context changed.
                    poster.post(
                        target.receiver,
                        factory.create(
                            InputPhase.RELEASE,
                            target.receiver,
                            position,
                            samples[index],
                            int(monotonic() * 1000),
                        ),
                        leave,
                        lambda cleanup_error: leave(),
                    )
                else:
                    leave()
            except Exception:
                leave()

        def delivered(phase: InputPhase) -> None:
            nonlocal index
            if phase is InputPhase.RELEASE:
                finish()
                return
            if index + 1 < len(samples):
                index += 1
                # Absolute deadlines avoid adding event delivery time to every sample.
                deadline_ms = started_ms + max(0, samples[index].timestamp_ms - source_ms)
                delay_ms = max(0, deadline_ms - int(monotonic() * 1000))
                self._scheduler.call_later(delay_ms, lambda: emit(InputPhase.MOVE))
            else:
                self._scheduler.call_soon(lambda: emit(InputPhase.RELEASE))

        position = (0.0, 0.0)

        def emit(phase: InputPhase) -> None:
            nonlocal pressed, position, started_ms
            if finished:
                return
            try:
                require_context()
                if not target.is_valid():
                    raise RuntimeError("the active canvas changed during replay")
                if phase is InputPhase.PRESS:
                    started_ms = int(monotonic() * 1000)
                sample = samples[index]
                position = target.to_widget((sample.x, sample.y))
                event = factory.create(
                    phase,
                    target.receiver,
                    position,
                    sample,
                    started_ms + max(0, sample.timestamp_ms - source_ms),
                )
                if phase is InputPhase.PRESS:
                    pressed = True
                post(event, lambda: delivered(phase))
                if phase is InputPhase.RELEASE:
                    pressed = False
            except Exception as error:
                finish(error)

        def prepare_press() -> None:
            try:
                require_context()
                if not target.is_valid():
                    raise RuntimeError("the active canvas changed during replay")
                if before_press is not None:
                    before_press()
                self._scheduler.call_soon(lambda: emit(InputPhase.PRESS))
            except Exception as error:
                finish(error)

        def hover() -> None:
            try:
                require_context()
                if not target.is_valid():
                    raise RuntimeError("the active canvas changed during replay")
                sample = samples[0]
                # The first pen event switches Krita's tool/device. Do that without
                # a pressed button, before applying settings or starting the stroke.
                event = factory.hover(
                    target.receiver,
                    target.to_widget((sample.x, sample.y)),
                    sample,
                    int(monotonic() * 1000),
                )
                post(event, prepare_press)
            except Exception as error:
                finish(error)

        def enter() -> None:
            nonlocal entered
            try:
                require_context()
                if not target.is_valid():
                    raise RuntimeError("the active canvas changed during replay")
                # Focus only after the Replay button's mouse handler has returned.
                target.focus()
                position = target.to_widget((samples[0].x, samples[0].y))
                # Krita ignores presses until its shortcut matcher sees a canvas enter.
                event = factory.enter(target.receiver, position)
                entered = True
                post(event, hover)
            except Exception as error:
                finish(error)

        self._scheduler.call_soon(enter)


class _DeliveryEvent(QtCore.QEvent):
    def __init__(self, callback, on_error) -> None:
        # This event goes only to our private receiver; no global type registration is needed.
        super().__init__(_qt_value(QtCore.QEvent, "Type", "User"))
        self.callback = callback
        self.on_error = on_error


class QtEventPoster(QtCore.QObject):
    """Acknowledge input after Qt returns from delivery, without nested event loops."""

    def post(self, receiver, event, on_delivered, on_error) -> None:
        QtCore.QCoreApplication.postEvent(receiver, event)
        # Equal-priority posted events are FIFO on the same GUI thread.
        QtCore.QCoreApplication.postEvent(self, _DeliveryEvent(on_delivered, on_error))

    def event(self, event) -> bool:
        if not isinstance(event, _DeliveryEvent):
            return super().event(event)
        try:
            event.callback()
        except Exception as error:
            try:
                event.on_error(error)
            except Exception:
                logger.exception("canvas replay callback failed")
        return True


class QtInputEventFactory:
    """Construct tablet input using Krita's loaded Qt binding and existing device."""

    @staticmethod
    def enter(receiver, position: tuple[float, float]):
        local = QtCore.QPointF(*position)
        point = local.toPoint()
        return QtGui.QEnterEvent(
            local,
            QtCore.QPointF(receiver.mapTo(receiver.window(), point)),
            QtCore.QPointF(receiver.mapToGlobal(point)),
        )

    @staticmethod
    def leave():
        return QtCore.QEvent(_qt_value(QtCore.QEvent, "Type", "Leave"))

    @staticmethod
    def hover(receiver, position, sample: BrushSample, timestamp_ms: int):
        return QtInputEventFactory.create(
            InputPhase.MOVE, receiver, position, sample, timestamp_ms, hover=True
        )

    @staticmethod
    def create(
        phase: InputPhase,
        receiver,
        position,
        sample: BrushSample,
        timestamp_ms: int,
        *,
        hover: bool = False,
    ):
        local = QtCore.QPointF(*position)
        global_position = QtCore.QPointF(receiver.mapToGlobal(local.toPoint()))
        event_type = _qt_value(
            QtCore.QEvent,
            "Type",
            {
                InputPhase.PRESS: "TabletPress",
                InputPhase.MOVE: "TabletMove",
                InputPhase.RELEASE: "TabletRelease",
            }[phase],
        )
        left = _qt_value(QtCore.Qt, "MouseButton", "LeftButton")
        none = _qt_value(QtCore.Qt, "MouseButton", "NoButton")
        button = none if phase is InputPhase.MOVE else left
        buttons = none if hover or phase is InputPhase.RELEASE else left
        modifiers = _qt_value(QtCore.Qt, "KeyboardModifier", "NoModifier")
        pressure = 0.0 if hover or phase is InputPhase.RELEASE else sample.pressure
        if hasattr(QtGui, "QPointingDevice"):
            event = QtGui.QTabletEvent(
                event_type,
                QtGui.QPointingDevice.primaryPointingDevice(),
                local,
                global_position,
                pressure,
                sample.x_tilt,
                sample.y_tilt,
                sample.tangential_pressure,
                sample.rotation,
                0.0,
                modifiers,
                button,
                buttons,
            )
        else:
            event = QtGui.QTabletEvent(
                event_type,
                local,
                global_position,
                _qt_value(QtGui.QTabletEvent, "TabletDevice", "Stylus"),
                _qt_value(QtGui.QTabletEvent, "PointerType", "Pen"),
                pressure,
                round(sample.x_tilt),
                round(sample.y_tilt),
                sample.tangential_pressure,
                sample.rotation,
                0,
                modifiers,
                0,
                button,
                buttons,
            )
        # Some Qt 6 Python bindings omit this setter; scheduling still preserves timing.
        set_timestamp = getattr(event, "setTimestamp", None)
        if callable(set_timestamp):
            set_timestamp(timestamp_ms & 0xFFFFFFFF)
        return event


def _qt_value(source, group: str, name: str):
    return getattr(getattr(source, group, source), name)
