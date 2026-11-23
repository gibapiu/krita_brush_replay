"""Qt-free state machine coordinating capture and replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from krita_brush_replay.app.recording import Recording
from krita_brush_replay.app.recording_store import RecordingStore, StorageBusyError
from krita_brush_replay.domain.brush_override import BrushStatePatch, RecordingSettings
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    InputPhase,
    RecordingSession,
)
from krita_brush_replay.domain.recording_history import HistoryStep


class ReplayHost(Protocol):
    def active_context(self) -> ContextRef | None: ...

    def capture_brush_state(self) -> BrushState: ...

    def replay(
        self,
        session: RecordingSession,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        use_original_settings: bool,
    ) -> None: ...


class ServiceMode(Enum):
    IDLE = auto()
    RECORDING = auto()
    REPLAYING = auto()


@dataclass(frozen=True, slots=True)
class ServiceState:
    """A status snapshot; recording data is queried separately."""

    mode: ServiceMode
    sample_count: int
    message: str
    stroke_count: int
    settings_readable: bool

    @property
    def can_record(self) -> bool:
        return self.mode is ServiceMode.IDLE

    @property
    def can_stop(self) -> bool:
        return self.mode is ServiceMode.RECORDING

    @property
    def can_replay(self) -> bool:
        return self.mode is ServiceMode.IDLE and self.stroke_count > 0

    @property
    def can_edit(self) -> bool:
        return self.can_replay and self.settings_readable


StateListener = Callable[[ServiceState], None]


class BrushReplayService:
    """Coordinate the recording and enforce its same-context boundary."""

    def __init__(self, host: ReplayHost, store: RecordingStore) -> None:
        self._host = host
        self._mode = ServiceMode.IDLE
        self._context: ContextRef | None = None
        self._recording = Recording(store)
        self._storage_failed = False
        self._closed = False
        self._active = False
        self._active_brush_state: BrushState | None = None
        self._message = "Ready. Record strokes on one paint layer."
        self._listeners: list[StateListener] = []
        self._settings = RecordingSettings()
        self._settings_revision: int | None = None
        self._settings_readable = True

    @property
    def state(self) -> ServiceState:
        unavailable = self._closed or self._storage_failed
        return ServiceState(
            mode=self._mode,
            sample_count=0 if unavailable else self._recording.sample_count,
            message=self._message,
            stroke_count=0 if unavailable else self._recording.stroke_count,
            settings_readable=self._settings_readable,
        )

    def recording_settings(self) -> RecordingSettings:
        return self._settings

    def _refresh_settings(self) -> None:
        if self._closed or self._storage_failed or self._mode is ServiceMode.RECORDING:
            self._settings = RecordingSettings()
            self._settings_revision = None
            self._settings_readable = True
            return
        if self._settings_revision == self._recording.revision:
            return
        try:
            self._settings = self._recording.settings()
        except (OSError, ValueError) as error:
            if self._settings_readable:
                self._message = f"Could not read brush settings: {error}"
            self._settings = RecordingSettings()
            self._settings_readable = False
            return
        self._settings_revision = self._recording.revision
        self._settings_readable = True

    @property
    def session(self) -> RecordingSession | None:
        if self._context is None:
            return None
        return RecordingSession(self._context, self._recording.strokes())

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)
        self._refresh_settings()
        listener(self.state)

    def unsubscribe(self, listener: StateListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def start_recording(self) -> None:
        if self._closed or self._mode is not ServiceMode.IDLE:
            return
        try:
            context = self._host.active_context()
        except Exception as error:
            self._set_message(f"Could not start recording: {error}")
            return
        if context is None:
            self._set_message("Open a document and select a writable paint layer first.")
            return
        try:
            self._recording.reset()
        except StorageBusyError as error:
            self._set_message(str(error))
            return
        except Exception as error:
            self._storage_failed = True
            self._context = None
            self._set_message(f"Could not start recording: {error}")
            return
        self._storage_failed = False
        self._context = context
        self._discard_active_stroke()
        self._mode = ServiceMode.RECORDING
        self._message = "Recording freehand brush strokes…"
        self._notify()

    def stop_recording(self) -> None:
        if self._closed or self._mode is not ServiceMode.RECORDING:
            return
        try:
            self._recording.stop()
            self._discard_active_stroke()
        except Exception as error:
            self._recording_failed(error)
            return
        self._mode = ServiceMode.IDLE
        self._message = f"Recorded {self._recording.stroke_count} stroke(s)."
        self._notify()

    def accept_input(
        self,
        phase: InputPhase,
        sample: BrushSample | None,
    ) -> None:
        try:
            self._accept_input(phase, sample)
        except OSError as error:
            self._recording_failed(error)

    def _accept_input(self, phase: InputPhase, sample: BrushSample | None) -> None:
        if self._closed or self._mode is not ServiceMode.RECORDING:
            return
        if phase is InputPhase.CANCEL:
            if self._active:
                self._discard_active_stroke()
                self._notify()
            return
        if phase in (InputPhase.PRESS, InputPhase.RELEASE):
            if self._host.active_context() != self._context:
                self._stop_for_context_change()
                return

        if phase is InputPhase.RELEASE:
            if self._active:
                if self._brush_was_resized():
                    self._discard_active_stroke()
                else:
                    self._finish_active_stroke()
                self._notify()
            return
        if sample is None:
            raise ValueError("press and move input require a brush sample")

        if phase is InputPhase.PRESS:
            self._finish_active_stroke()
            self._active_brush_state = self._host.capture_brush_state()
            self._recording.begin(self._active_brush_state)
            self._active = True
            self._recording.append(sample)
            self._notify()
        elif self._active:
            self._recording.append(sample)

    def undo_triggered(self) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        try:
            if not self._active:
                self._recording.history(HistoryStep.UNDO)
            else:
                self._discard_active_stroke()
        except Exception as error:
            self.capture_failed(error)
            return
        self._notify()

    def redo_triggered(self) -> None:
        if self._mode is not ServiceMode.RECORDING or self._active:
            return
        try:
            self._recording.history(HistoryStep.REDO)
        except Exception as error:
            self.capture_failed(error)
            return
        self._notify()

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
        try:
            self._recording.stop()
        except Exception as storage_error:
            error = storage_error
        self._recording_failed(error)

    def _recording_failed(self, error: Exception) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        self._discard_active_stroke()
        try:
            self._recording.abort()
        except Exception as storage_error:
            self._storage_failed = True
            self._context = None
            error = storage_error
        self._mode = ServiceMode.IDLE
        self._message = (
            str(error) if isinstance(error, TimeoutError) else f"Recording stopped: {error}"
        )
        if not self._storage_failed:
            self._message += f" Kept {self._recording.stroke_count} stroke(s)."
        self._notify()

    def override_setting(self, setting: str, value: object) -> None:
        self._edit_settings(lambda settings: settings.overriding(setting, value))

    def reset_setting(self, setting: str) -> None:
        self._edit_settings(lambda settings: settings.override.without_setting(setting))

    def replay(self) -> None:
        self._replay(use_original_settings=True)

    def replay_with_current_settings(self) -> None:
        self._replay(use_original_settings=False)

    def _replay(self, *, use_original_settings: bool) -> None:
        if self._closed or self._mode is not ServiceMode.IDLE:
            return
        session = self.session
        if session is None or not session.strokes:
            return
        if use_original_settings:
            session = RecordingSession(session.origin, self._recording.strokes(effective=True))
        try:
            self._mode = ServiceMode.REPLAYING
            settings_name = "stroke" if use_original_settings else "current"
            self._message = f"Replaying on the active layer with {settings_name} brush settings…"
            self._notify()
            self._host.replay(
                session,
                self._replay_completed,
                self._replay_failed,
                use_original_settings=use_original_settings,
            )
        except Exception as error:
            self._replay_failed(error)

    def _edit_settings(self, edit: Callable[[RecordingSettings], BrushStatePatch]) -> None:
        if not self.state.can_edit:
            return
        try:
            self._recording.edit(edit)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            self._set_message(f"Could not change brush settings: {error}")
            return
        self._notify()

    def _finish_active_stroke(self) -> None:
        if self._active:
            self._recording.finish()
        self._discard_active_stroke()

    def _brush_was_resized(self) -> bool:
        return (
            self._active_brush_state is not None
            and self._host.capture_brush_state().size != self._active_brush_state.size
        )

    def _discard_active_stroke(self) -> None:
        self._recording.discard()
        self._active = False
        self._active_brush_state = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._listeners.clear()
        self._discard_active_stroke()
        if self._mode is not ServiceMode.REPLAYING:
            self._mode = ServiceMode.IDLE
            self._recording.close()

    def _stop_for_context_change(self) -> None:
        self._discard_active_stroke()
        try:
            self._recording.stop()
        except Exception as error:
            self._recording_failed(error)
            return
        self._mode = ServiceMode.IDLE
        self._message = "Recording stopped because the document or layer changed."
        self._notify()

    def _replay_completed(self) -> None:
        if self._closed:
            self._recording.close()
            return
        self._mode = ServiceMode.IDLE
        self._message = "Replay complete. Your pre-replay brush settings were restored."
        self._notify()

    def _replay_failed(self, error: Exception) -> None:
        if self._closed:
            self._recording.close()
            return
        self._mode = ServiceMode.IDLE
        self._message = f"Replay failed: {error}"
        self._notify()

    def _set_message(self, message: str) -> None:
        self._message = message
        self._notify()

    def _notify(self) -> None:
        self._refresh_settings()
        state = self.state
        for listener in tuple(self._listeners):
            listener(state)
