
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.domain.recording import BrushSample, InputPhase
from krita_brush_replay.infrastructure.host.krita_replay import Scheduler
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, QtGui

@dataclass(frozen=True, slots=True)
class CanvasInputTarget:

    receiver: object
    to_widget: Callable[[tuple[float, float]], tuple[float, float]]
    is_valid: Callable[[], bool]
    focus: Callable[[], None]


def _require_gui_thread() -> None:
    application = QtCore.QCoreApplication.instance()
    if application is None or QtCore.QThread.currentThread() != application.thread():
        raise RuntimeError("canvas replay must run on Krita's GUI thread")


class SyntheticInputReplayer:

    def __init__(
        self,
        target_provider: Callable[[], CanvasInputTarget],
        scheduler: Scheduler,
        *,
        event_factory: QtInputEventFactory | None = None,
        event_poster: QtEventPoster | None = None,
        report_issue: IssueReporter = ignore_issue,
    ) -> None:
        self._target_provider = target_provider
        self._scheduler = scheduler
        self._event_factory = event_factory if event_factory is not None else QtInputEventFactory()
        self._event_poster = event_poster
        self._report_issue = report_issue

    def prepare(self) -> CanvasInputTarget:
        _require_gui_thread()
        target = self._target_provider()
        if not target.is_valid():
            raise RuntimeError("the active canvas changed before replay")
        if self._event_poster is None:
            self._event_poster = QtEventPoster(report_issue=self._report_issue)
        return target

    def replay_stroke(
        self,
        target: CanvasInputTarget,
        samples: Sequence[BrushSample],
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
                self._report_issue("synthetic-cleanup-failed", cleanup_error)
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

            def release_failed(cleanup_error: Exception) -> None:
                self._report_issue("synthetic-release-failed", cleanup_error)
                leave()

            try:
                if pressed:
                    pressed = False
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
                        release_failed,
                    )
                else:
                    leave()
            except Exception as cleanup_error:
                release_failed(cleanup_error)

        def delivered(phase: InputPhase) -> None:
            nonlocal index
            if phase is InputPhase.RELEASE:
                finish()
                return
            if index + 1 < len(samples):
                index += 1
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

        def initialize_pen_device() -> None:
            try:
                require_context()
                if not target.is_valid():
                    raise RuntimeError("the active canvas changed during replay")
                sample = samples[0]
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
                target.focus()
                position = target.to_widget((samples[0].x, samples[0].y))
                event = factory.enter(target.receiver, position)
                entered = True
                post(event, initialize_pen_device)
            except Exception as error:
                finish(error)

        self._scheduler.call_soon(enter)


class _DeliveryEvent(QtCore.QEvent):
    def __init__(self, callback, on_error) -> None:
        super().__init__(_qt_value(QtCore.QEvent, "Type", "User"))
        self.callback = callback
        self.on_error = on_error


class QtEventPoster(QtCore.QObject):

    def __init__(self, *, report_issue: IssueReporter = ignore_issue) -> None:
        super().__init__()
        self._report_issue = report_issue

    def post(self, receiver, event, on_delivered, on_error) -> None:
        QtCore.QCoreApplication.postEvent(receiver, event)
        QtCore.QCoreApplication.postEvent(self, _DeliveryEvent(on_delivered, on_error))

    def event(self, event) -> bool:
        if not isinstance(event, _DeliveryEvent):
            return super().event(event)
        try:
            event.callback()
        except Exception as error:
            self._report_issue("event-delivery-callback-failed", error)
            try:
                event.on_error(error)
            except Exception as callback_error:
                self._report_issue("event-delivery-error-callback-failed", callback_error)
        return True


class QtInputEventFactory:

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
        set_timestamp = getattr(event, "setTimestamp", None)
        if callable(set_timestamp):
            set_timestamp(timestamp_ms & 0xFFFFFFFF)
        return event


def _qt_value(source, group: str, name: str):
    return getattr(getattr(source, group, source), name)
