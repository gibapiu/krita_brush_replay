"""Capability-aware Krita stroke replay."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Protocol

from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    RecordingSession,
)

REPLAY_COMPLETION_POLL_MS = 10
REPLAY_COMPLETION_TIMEOUT_MS = 120_000
PointFactory = Callable[[float, float], object]
StrokePointFactory = Callable[..., object]
ContextProvider = Callable[[], ContextRef | None]
BrushStateApplier = Callable[[BrushState], None]
PressureMapper = Callable[[float], float]
PressureMapperProvider = Callable[[], PressureMapper]


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
        pressure_mapper_provider: PressureMapperProvider | None = None,
    ) -> None:
        self._context_provider = context_provider
        self._float_point_factory = float_point_factory
        self._stroke_point_factory = stroke_point_factory
        self._scheduler = scheduler if scheduler is not None else QtScheduler()
        self._synthetic_input = synthetic_input
        self._pressure_mapper_provider = pressure_mapper_provider

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
        if self._synthetic_input is not None:
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
        strokes = iter(session.strokes)

        def process_stroke() -> None:
            try:
                self._require_context(target, "during replay")
                try:
                    stroke = next(strokes)
                except StopIteration:
                    on_complete()
                    return
                self._scheduler.call_soon(lambda: submit_stroke(stroke))
            except Exception as error:
                on_error(error)

        def submit_stroke(stroke) -> None:
            try:
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
        try:
            pressure_mapper = (
                self._pressure_mapper_provider()
                if self._pressure_mapper_provider is not None
                else float
            )
        except Exception as error:
            on_error(error)
            return
        strokes = iter(session.strokes)

        def submit_stroke(stroke) -> None:
            try:
                self._require_context(target, "during replay")
                method(
                    self._stroke_points(
                        stroke.samples,
                        stroke_point_factory,
                        float_point_factory,
                        pressure_mapper,
                    )
                )
                self._scheduler.call_soon(process_stroke)
            except Exception as error:
                on_error(error)

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
                    self._scheduler.call_soon(lambda: submit_stroke(stroke))
                    return
                submit_stroke(stroke)
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
        pressure_mapper: PressureMapper = float,
    ) -> list[object]:
        started_at = samples[0].timestamp_ms
        return [
            stroke_point_factory(
                float_point_factory(sample.x, sample.y),
                pressure_mapper(sample.pressure),
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
