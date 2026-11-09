"""Qt-free state machine coordinating capture and replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from krita_brush_replay.domain.recording import (
    BrushSample,
    ContextRef,
    InputPhase,
    RecordingSession,
    Stroke,
)


class ReplayHost(Protocol):
    def active_context(self) -> ContextRef | None: ...

    def replay(
        self,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None: ...


class ServiceMode(Enum):
    IDLE = auto()
    RECORDING = auto()
    REPLAYING = auto()


@dataclass(frozen=True, slots=True)
class ServiceState:
    mode: ServiceMode
    stroke_count: int
    sample_count: int
    message: str

    @property
    def can_record(self) -> bool:
        return self.mode is ServiceMode.IDLE

    @property
    def can_stop(self) -> bool:
        return self.mode is ServiceMode.RECORDING

    @property
    def can_replay(self) -> bool:
        return self.mode is ServiceMode.IDLE and self.stroke_count > 0


StateListener = Callable[[ServiceState], None]


class BrushReplayService:
    """Own the in-memory recording and enforce its same-context boundary."""

    def __init__(self, host: ReplayHost) -> None:
        self._host = host
        self._mode = ServiceMode.IDLE
        self._context: ContextRef | None = None
        self._strokes: list[Stroke] = []
        self._active_samples: list[BrushSample] | None = None
        self._message = "Ready. Record strokes on one paint layer."
        self._listeners: list[StateListener] = []

    @property
    def state(self) -> ServiceState:
        sample_count = sum(len(stroke.samples) for stroke in self._strokes)
        if self._active_samples is not None:
            sample_count += len(self._active_samples)
        return ServiceState(
            mode=self._mode,
            stroke_count=len(self._strokes),
            sample_count=sample_count,
            message=self._message,
        )

    @property
    def session(self) -> RecordingSession | None:
        if self._context is None:
            return None
        return RecordingSession(self._context, tuple(self._strokes))

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)
        listener(self.state)

    def unsubscribe(self, listener: StateListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def start_recording(self) -> None:
        if self._mode is not ServiceMode.IDLE:
            return
        try:
            context = self._host.active_context()
        except Exception as error:
            self._set_message(f"Could not start recording: {error}")
            return
        if context is None:
            self._set_message("Open a document and select a writable paint layer first.")
            return
        self._context = context
        self._strokes.clear()
        self._discard_active_stroke()
        self._mode = ServiceMode.RECORDING
        self._message = "Recording freehand brush strokes…"
        self._notify()

    def stop_recording(self) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        self._finish_active_stroke()
        self._mode = ServiceMode.IDLE
        self._message = f"Recorded {len(self._strokes)} stroke(s)."
        self._notify()

    def accept_input(
        self,
        phase: InputPhase,
        sample: BrushSample | None,
    ) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        if phase in (InputPhase.PRESS, InputPhase.RELEASE):
            if self._host.active_context() != self._context:
                self._stop_for_context_change()
                return

        if phase is InputPhase.RELEASE:
            if self._active_samples is not None:
                self._finish_active_stroke()
                self._notify()
            return
        if sample is None:
            raise ValueError("press and move input require a brush sample")

        if phase is InputPhase.PRESS:
            self._finish_active_stroke()
            self._active_samples = [sample]
            self._notify()
        elif self._active_samples is not None:
            self._active_samples.append(sample)

    def canvas_changed(self) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        try:
            changed = self._host.active_context() != self._context
        except Exception as error:
            self.capture_failed(error)
            return
        if changed:
            self._stop_for_context_change()

    def capture_failed(self, error: Exception) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        self._discard_active_stroke()
        self._mode = ServiceMode.IDLE
        self._message = f"Recording stopped: {error}"
        self._notify()

    def replay(self) -> None:
        if self._mode is not ServiceMode.IDLE:
            return
        session = self.session
        if session is None or not session.strokes:
            return
        try:
            if self._host.active_context() != session.origin:
                self._set_message(
                    "Replay requires the same document and layer used for recording."
                )
                return
            self._mode = ServiceMode.REPLAYING
            self._message = "Replaying with the current brush settings…"
            self._notify()
            self._host.replay(
                session,
                session.origin,
                self._replay_completed,
                self._replay_failed,
            )
        except Exception as error:
            self._replay_failed(error)

    def _finish_active_stroke(self) -> None:
        if self._active_samples:
            self._strokes.append(Stroke(tuple(self._active_samples)))
        self._discard_active_stroke()

    def _discard_active_stroke(self) -> None:
        self._active_samples = None

    def _stop_for_context_change(self) -> None:
        self._discard_active_stroke()
        self._mode = ServiceMode.IDLE
        self._message = "Recording stopped because the document or layer changed."
        self._notify()

    def _replay_completed(self) -> None:
        self._mode = ServiceMode.IDLE
        self._message = "Replay complete. Current brush settings were used."
        self._notify()

    def _replay_failed(self, error: Exception) -> None:
        self._mode = ServiceMode.IDLE
        self._message = f"Replay failed: {error}"
        self._notify()

    def _set_message(self, message: str) -> None:
        self._message = message
        self._notify()

    def _notify(self) -> None:
        state = self.state
        for listener in tuple(self._listeners):
            listener(state)
