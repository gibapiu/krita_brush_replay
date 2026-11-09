"""Capability-aware Krita stroke replay."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from time import monotonic
from typing import Protocol

from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    RecordingSession,
)

DEFAULT_REPLAY_BATCH_SIZE = 32
REPLAY_COMPLETION_POLL_MS = 10
REPLAY_COMPLETION_TIMEOUT_MS = 120_000
PointFactory = Callable[[float, float], object]
NamedPointFactory = tuple[str, PointFactory]
StrokePointFactory = Callable[..., object]
ContextProvider = Callable[[], ContextRef | None]
BrushStateApplier = Callable[[BrushState], None]


class Scheduler(Protocol):
    def call_soon(self, callback: Callable[[], None]) -> None: ...

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None: ...


class QtScheduler:
    def call_soon(self, callback: Callable[[], None]) -> None:
        self.call_later(0, callback)

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None:
        from krita_brush_replay.infrastructure.qt_facade import QtCore

        QtCore.QTimer.singleShot(delay_ms, callback)


class KritaReplayer:
    """Submit complete recorded strokes through the best available Krita API."""

    def __init__(
        self,
        context_provider: ContextProvider,
        *,
        point_factories: tuple[NamedPointFactory, ...] | None = None,
        float_point_factory: PointFactory | None = None,
        stroke_point_factory: StrokePointFactory | None = None,
        scheduler: Scheduler | None = None,
        replay_batch_size: int = DEFAULT_REPLAY_BATCH_SIZE,
    ) -> None:
        self._context_provider = context_provider
        self._resolved_line_point_factory: PointFactory | None = None
        self._point_factories = point_factories
        self._float_point_factory = float_point_factory
        self._stroke_point_factory = stroke_point_factory
        self._scheduler = scheduler if scheduler is not None else QtScheduler()
        self._replay_batch_size = max(1, replay_batch_size)

    def replay(
        self,
        document,
        node,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        apply_brush_state: BrushStateApplier | None = None,
    ) -> None:
        continuous_api = self._continuous_replay_api(node)
        if continuous_api is not None:
            method, stroke_point_factory, float_point_factory = continuous_api
            self._replay_continuous(
                document,
                session,
                target,
                method,
                stroke_point_factory,
                float_point_factory,
                on_complete,
                on_error,
                apply_brush_state,
            )
            return
        self._replay_with_lines(
            document,
            node,
            session,
            target,
            on_complete,
            on_error,
            apply_brush_state,
        )

    def _replay_continuous(
        self,
        document,
        session: RecordingSession,
        target: ContextRef,
        method,
        stroke_point_factory: StrokePointFactory,
        float_point_factory: PointFactory,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        apply_brush_state: BrushStateApplier | None,
    ) -> None:
        strokes = iter(session.strokes)

        def process_stroke() -> None:
            try:
                self._require_context(target, "during replay")
                try:
                    stroke = next(strokes)
                except StopIteration:
                    self._wait_for_replay(
                        document,
                        target,
                        on_complete,
                        on_error,
                    )
                    return
                if apply_brush_state is not None:
                    apply_brush_state(stroke.brush_state)
                method(
                    self._stroke_points(
                        stroke.samples,
                        stroke_point_factory,
                        float_point_factory,
                    )
                )
                self._scheduler.call_soon(process_stroke)
            except Exception as error:
                on_error(error)

        self._scheduler.call_soon(process_stroke)

    def _replay_with_lines(
        self,
        document,
        node,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        apply_brush_state: BrushStateApplier | None,
    ) -> None:
        strokes = iter(session.strokes)
        segments: Iterator[tuple[BrushSample, BrushSample]] | None = None

        def next_segment() -> tuple[BrushSample, BrushSample]:
            nonlocal segments
            while True:
                if segments is None:
                    stroke = next(strokes)
                    if apply_brush_state is not None:
                        apply_brush_state(stroke.brush_state)
                    segments = iter(_stroke_segments(stroke.samples))
                try:
                    return next(segments)
                except StopIteration:
                    segments = None

        def process_batch() -> None:
            try:
                self._require_context(target, "during replay")
                for _ in range(self._replay_batch_size):
                    try:
                        start, end = next_segment()
                    except StopIteration:
                        self._wait_for_replay(
                            document,
                            target,
                            on_complete,
                            on_error,
                        )
                        return
                    self._paint_line(node.paintLine, start, end)
                self._scheduler.call_soon(process_batch)
            except Exception as error:
                on_error(error)

        self._scheduler.call_soon(process_batch)

    def _continuous_replay_api(self, node):
        method = getattr(node, "paintPolylineStroke", None)
        if not callable(method):
            return None

        stroke_point_factory = self._stroke_point_factory
        if stroke_point_factory is None:
            try:
                from krita import StrokePoint
            except ImportError:
                return None
            stroke_point_factory = StrokePoint

        float_point_factory = self._float_point_factory
        if float_point_factory is None:
            from krita_brush_replay.infrastructure.qt_facade import QtCore

            float_point_factory = QtCore.QPointF
        return method, stroke_point_factory, float_point_factory

    @staticmethod
    def _stroke_points(
        samples: tuple[BrushSample, ...],
        stroke_point_factory: StrokePointFactory,
        float_point_factory: PointFactory,
    ) -> list[object]:
        started_at = samples[0].timestamp_ms
        return [
            stroke_point_factory(
                float_point_factory(sample.x, sample.y),
                sample.pressure,
                sample.x_tilt,
                sample.y_tilt,
                sample.rotation,
                sample.tangential_pressure,
                float(max(0, sample.timestamp_ms - started_at)),
                sample.speed,
            )
            for sample in samples
        ]

    def _paint_line(
        self,
        method,
        start: BrushSample,
        end: BrushSample,
    ) -> None:
        if self._resolved_line_point_factory is not None:
            self._call_paint_line(method, self._resolved_line_point_factory, start, end)
            return

        last_error: TypeError | None = None
        for _name, factory in self._ordered_line_point_factories(method):
            try:
                self._call_paint_line(method, factory, start, end)
            except TypeError as error:
                if not _is_binding_point_type_error(error):
                    raise
                last_error = error
                continue
            self._resolved_line_point_factory = factory
            return
        if last_error is not None:
            raise last_error
        raise RuntimeError("Krita provides no compatible point type for paintLine")

    @staticmethod
    def _call_paint_line(
        method,
        factory: PointFactory,
        start: BrushSample,
        end: BrushSample,
    ) -> None:
        method(
            factory(start.x, start.y),
            factory(end.x, end.y),
            start.pressure,
            end.pressure,
        )

    def _ordered_line_point_factories(self, method) -> tuple[NamedPointFactory, ...]:
        factories = self._point_factories
        if factories is None:
            from krita_brush_replay.infrastructure.qt_facade import QtCore

            factories = (
                ("QPoint", lambda x, y: _create_qpoint(QtCore.QPoint, x, y)),
                ("QPointF", QtCore.QPointF),
            )
        signature = getattr(method, "__doc__", "") or ""
        preferred = "QPointF" if "QPointF" in signature else "QPoint"
        return tuple(sorted(factories, key=lambda item: item[0] != preferred))

    def _require_context(self, expected: ContextRef, suffix: str) -> None:
        if self._context_provider() != expected:
            raise RuntimeError(f"the active document or layer changed {suffix}")

    def _wait_for_replay(
        self,
        document,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        try_lock = getattr(document, "tryBarrierLock", None)
        unlock = getattr(document, "unlock", None)
        if not callable(try_lock) or not callable(unlock):
            on_error(RuntimeError("Krita cannot report replay completion"))
            return
        deadline = monotonic() + REPLAY_COMPLETION_TIMEOUT_MS / 1000

        def poll() -> None:
            try:
                self._require_context(target, "while finishing replay")
                if not try_lock():
                    if monotonic() >= deadline:
                        raise TimeoutError("Krita did not finish replay in time")
                    self._scheduler.call_later(REPLAY_COMPLETION_POLL_MS, poll)
                    return
                unlock()
                on_complete()
            except Exception as error:
                on_error(error)

        self._scheduler.call_soon(poll)


def _stroke_segments(
    samples: tuple[BrushSample, ...],
) -> Iterator[tuple[BrushSample, BrushSample]]:
    if len(samples) == 1:
        yield samples[0], samples[0]
        return
    yield from zip(samples, samples[1:])


def _create_qpoint(qpoint_type, x: float, y: float):
    """Create an integer point for released Krita paintLine bindings."""

    return qpoint_type(round(x), round(y))


def _is_binding_point_type_error(error: TypeError) -> bool:
    message = str(error)
    return "unexpected type" in message and (
        "argument 1" in message or "argument 2" in message
    )
