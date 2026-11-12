"""Capability-aware Krita stroke replay."""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import Protocol

from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    RecordingSession,
)
from krita_brush_replay.infrastructure.diagnostics import PressureTrace

logger = logging.getLogger(__name__)

REPLAY_COMPLETION_POLL_MS = 10
REPLAY_COMPLETION_TIMEOUT_MS = 120_000
PointFactory = Callable[[float, float], object]
StrokePointFactory = Callable[..., object]
ContextProvider = Callable[[], ContextRef | None]
BrushStateApplier = Callable[[BrushState], None]


class Scheduler(Protocol):
    def call_soon(self, callback: Callable[[], None]) -> None: ...

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None: ...


class SyntheticStrokeInput(Protocol):
    def prepare(self): ...

    def replay_stroke(
        self,
        target,
        samples: tuple[BrushSample, ...],
        require_context: Callable[[], None],
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        before_press: Callable[[], None] | None = None,
    ) -> None: ...


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
        float_point_factory: PointFactory | None = None,
        stroke_point_factory: StrokePointFactory | None = None,
        scheduler: Scheduler | None = None,
        synthetic_input: SyntheticStrokeInput | None = None,
    ) -> None:
        self._context_provider = context_provider
        self._float_point_factory = float_point_factory
        self._stroke_point_factory = stroke_point_factory
        self._scheduler = scheduler if scheduler is not None else QtScheduler()
        self._synthetic_input = synthetic_input

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
            logger.info("replay-api api=paintPolylineStroke")
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
        if self._synthetic_input is not None:
            logger.info("replay-api api=synthetic-tablet-events")
            self._replay_synthetic(
                document,
                session,
                target,
                on_complete,
                on_error,
                apply_brush_state,
            )
            return
        on_error(RuntimeError("Krita provides no continuous stroke replay capability"))

    def _replay_synthetic(
        self,
        document,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        apply_brush_state: BrushStateApplier | None,
    ) -> None:
        synthetic_input = self._synthetic_input
        if synthetic_input is None:
            raise RuntimeError("synthetic input is unavailable")
        try:
            canvas_target = synthetic_input.prepare()
        except Exception as error:
            on_error(error)
            return
        strokes = iter(enumerate(session.strokes, start=1))

        def process_stroke() -> None:
            try:
                self._require_context(target, "during replay")
                try:
                    index, stroke = next(strokes)
                except StopIteration:
                    on_complete()
                    return
                self._scheduler.call_soon(lambda: submit_stroke(index, stroke))
            except Exception as error:
                on_error(error)

        def submit_stroke(index, stroke) -> None:
            try:
                _log_stroke(index, stroke.samples)
                synthetic_input.replay_stroke(
                    canvas_target,
                    stroke.samples,
                    lambda: self._require_context(target, "during replay"),
                    lambda: self._wait_for_replay(
                        document,
                        target,
                        process_stroke,
                        on_error,
                    ),
                    on_error,
                    before_press=(
                        (lambda: apply_brush_state(stroke.brush_state))
                        if apply_brush_state is not None
                        else None
                    ),
                )
            except Exception as error:
                on_error(error)

        self._scheduler.call_soon(process_stroke)

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
        strokes = iter(enumerate(session.strokes, start=1))

        def submit_stroke(index, stroke) -> None:
            try:
                self._require_context(target, "during replay")
                _log_stroke(index, stroke.samples)
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

        def process_stroke() -> None:
            try:
                self._require_context(target, "during replay")
                try:
                    index, stroke = next(strokes)
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
                    self._scheduler.call_soon(lambda: submit_stroke(index, stroke))
                    return
                submit_stroke(index, stroke)
            except Exception as error:
                on_error(error)

        self._scheduler.call_soon(process_stroke)

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


def _log_stroke(index: int, samples: tuple[BrushSample, ...]) -> None:
    trace = PressureTrace()
    for sample in samples:
        trace.observe(sample)
    logger.info("replay-stroke index=%s %s", index, trace)
