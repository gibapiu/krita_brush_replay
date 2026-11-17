
from __future__ import annotations

from collections.abc import Callable, Iterable
from time import monotonic
from typing import Protocol

from krita_brush_replay.app.recording_store import Recording
from krita_brush_replay.app.recording_workspace import RecordingWorkspace
from krita_brush_replay.app.recording_library import RecordingBrowser, RecordingKey
from krita_brush_replay.app.replay_selection import ReplaySelection
from krita_brush_replay.app.service_state import READY, ServiceMode, ServiceState
from krita_brush_replay.domain.brush_override import StrokeSettings
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ContextRef,
    HistoryStep,
    InputPhase,
    RecordingSession,
    Stroke,
)
from krita_brush_replay.domain.recording_model import StrokeCapture


class ReplayHost(Protocol):
    def active_context(self) -> ContextRef | None: ...

    def active_document_id(self) -> str | None: ...

    def document_path(self, document_id: str) -> str | None: ...

    def capture_brush_state(self) -> BrushState: ...

    def replay(
        self,
        session: RecordingSession,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        use_original_settings: bool,
    ) -> None: ...

    def cancel_replay(self) -> None: ...


class BrushReplayService:
    def __init__(
        self,
        host: ReplayHost,
        workspace: RecordingWorkspace,
        on_change: Callable[[ServiceState], None],
        library: RecordingBrowser,
    ) -> None:
        self._host = host
        self._workspace = workspace
        self._on_change = on_change
        self.library = library
        self._source = ReplaySelection(workspace, library)
        self._capture = StrokeCapture()
        self._mode = ServiceMode.IDLE
        self._message = READY
        workspace.watch_failures(self._saving_failed)

    @property
    def state(self) -> ServiceState:
        model = self._workspace.model if self._mode is ServiceMode.RECORDING else self._source.model
        return ServiceState(
            self._mode,
            model.settings,
            model.sample_count + self._capture.sample_count,
            self._message,
            self._source.key,
            self._workspace.key,
            self._workspace.document,
            len(self._workspace.model.settings),
            self._workspace.model.sample_count + self._capture.sample_count,
            self._workspace.document_id is not None,
        )

    def start_recording(self) -> None:
        if self._mode is not ServiceMode.IDLE:
            return
        try:
            context = self._host.active_context()
            document = None if context is None else self._host.document_path(context.document_id)
        except Exception as error:
            self._message_changed(f"Could not start recording: {error}")
            return
        if context is None:
            self._message_changed("Open a document and select a writable paint layer first.")
            return
        self._source.clear()
        self._workspace.start(document, context)
        self._capture.discard()
        self._mode = ServiceMode.RECORDING
        self._message_changed(self._workspace.status)

    def stop_recording(self) -> None:
        if self._mode is ServiceMode.RECORDING:
            self._commit(self._capture.finish())
            self._end_recording(f"Recorded {len(self._workspace.model.settings)} stroke(s).")

    def close(self, timeout_s: float | None = None) -> bool:
        self.stop_recording()
        deadline = None if timeout_s is None else monotonic() + timeout_s
        replaying = self._mode in (ServiceMode.REPLAYING, ServiceMode.CLOSING)
        if replaying:
            self._mode = ServiceMode.CLOSING
            self._host.cancel_replay()
        else:
            self._mode = ServiceMode.CLOSED
            self._source.clear()
        library_saved = self.library.close(timeout_s, keep=self._source.recording if replaying else None)
        remaining = None if deadline is None else max(0, deadline - monotonic())
        workspace_saved = self._workspace.close(remaining, keep_replay=replaying and self._source.current)
        return library_saved and workspace_saved

    def select_recording(self, key: RecordingKey) -> None:
        if self._mode is not ServiceMode.IDLE:
            return
        if key == self._source.key:
            return
        if key == self._workspace.key:
            self._source.select_current()
            self._mode = ServiceMode.IDLE
            self._message_changed("Ready to replay.")
            return
        self._source.clear()
        self._mode = ServiceMode.LOADING
        self._message_changed("Loading…")

        def loaded(saved):
            if self._mode in (ServiceMode.CLOSING, ServiceMode.CLOSED):
                self.library.release(saved.recording)
                return
            self._source.select(key, saved)
            self._mode = ServiceMode.IDLE
            self._select_document()
            self._message_changed("Recovered recording." if saved.recovered else "Ready to replay.")

        def failed(error):
            if self._mode is ServiceMode.LOADING:
                self._mode = ServiceMode.IDLE
                self._message_changed(str(error))

        self.library.load(key, loaded, failed)

    def delete_recordings(self, keys: tuple[RecordingKey, ...]) -> None:
        if self._mode is not ServiceMode.IDLE or not keys:
            return
        released = []
        if self._source.key in keys:
            recording = self._source.clear()
            if recording is not None:
                released.append(recording)
        if self._workspace.key in keys:
            recording = self._workspace.forget()
            if recording is not None:
                released.append(recording)
        self._mode = ServiceMode.LOADING
        self._message_changed("Deleting…")

        def finished():
            if self._mode is ServiceMode.LOADING:
                self._mode = ServiceMode.IDLE
                self._message_changed("Ready to replay." if self._source.key else "Choose a recording.")

        self.library.remove(tuple(keys), tuple(released), finished)

    def document_saved(self) -> None:
        if self._mode in (ServiceMode.CLOSED, ServiceMode.CLOSING):
            return
        document = self._host.document_path(self._workspace.document_id)
        if document:
            changed = self._workspace.follow(document)
            if changed and self._mode is ServiceMode.RECORDING:
                self._message_changed(self._workspace.status)
            else:
                self._emit()

    def accept_input(self, phase: InputPhase, sample: BrushSample | None) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        if phase in (InputPhase.PRESS, InputPhase.RELEASE):
            if self._host.active_context() != self._workspace.model.origin:
                self._stop_for_context_change()
                return
        if phase is InputPhase.RELEASE:
            if self._capture.active:
                self._complete_gesture()
                self._emit()
            return
        if sample is None:
            raise ValueError("press and move input require a brush sample")
        if phase is InputPhase.PRESS:
            self._commit(self._capture.finish())
            self._capture.begin(sample, self._host.capture_brush_state())
            self._emit()
        else:
            self._capture.move(sample)
        self._check_limit()

    def undo_triggered(self) -> None:
        if self._mode is not ServiceMode.RECORDING:
            return
        if self._capture.active:
            self._capture.discard()
        else:
            self._append(HistoryStep.UNDO)
        self._emit()

    def redo_triggered(self) -> None:
        if self._mode is ServiceMode.RECORDING and not self._capture.active:
            self._append(HistoryStep.REDO)
            self._emit()

    def canvas_changed(self) -> None:
        if self._mode is ServiceMode.RECORDING:
            try:
                changed = self._host.active_context() != self._workspace.model.origin
            except Exception as error:
                self.capture_failed(error)
            else:
                if changed:
                    self._stop_for_context_change()
        if self._mode is ServiceMode.IDLE:
            self._select_document()

    def capture_failed(self, error: Exception) -> None:
        if self._mode is ServiceMode.RECORDING:
            self._capture.discard()
            self._end_recording(f"Recording stopped: {error}")

    def override_setting(self, indexes: Iterable[int], setting: str, value: object) -> None:
        self._edit(indexes, lambda stroke: stroke.overriding(setting, value))

    def reset_setting(self, indexes: Iterable[int], setting: str) -> None:
        self._edit(indexes, lambda stroke: stroke.resetting(setting))

    def replay(self) -> None:
        self._replay(edited=True)

    def replay_with_current_settings(self) -> None:
        self._replay(edited=False)

    def _replay(self, *, edited: bool) -> None:
        if self._mode is not ServiceMode.IDLE:
            return
        if self._source.key is None:
            return
        session = self._source.model.session(edited=edited)
        if session is None or not session.strokes:
            return
        try:
            self._mode = ServiceMode.REPLAYING
            settings = "stroke" if edited else "current"
            self._message_changed(f"Replaying on the active layer with {settings} brush settings…")
            self._host.replay(
                session,
                self._replay_completed,
                self._replay_failed,
                use_original_settings=edited,
            )
        except Exception as error:
            self._replay_failed(error)

    def _edit(
        self, indexes: Iterable[int], change: Callable[[StrokeSettings], StrokeSettings]
    ) -> None:
        if self._mode is not ServiceMode.IDLE or self._source.key is None:
            return
        try:
            self._source.edit(indexes, change)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            self._message_changed(f"Could not change stroke settings: {error}")
            return
        self._emit()

    def _complete_gesture(self) -> None:
        size = self._host.capture_brush_state().size if self._capture.active else None
        self._commit(self._capture.finish(size))

    def _commit(self, stroke: Stroke | None) -> None:
        if stroke is not None:
            self._append(stroke)

    def _append(self, entry: Stroke | HistoryStep) -> None:
        warning = self._workspace.append(entry)
        if warning is not None:
            self._message = warning

    def _check_limit(self) -> None:
        reason = self._workspace.stop_reason(self._capture.sample_count)
        if reason is not None:
            self._complete_gesture()
            self._end_recording(reason)

    def _end_recording(self, message: str) -> None:
        self._mode = ServiceMode.IDLE
        self._workspace.seal()
        self._message_changed(message)

    def _stop_for_context_change(self) -> None:
        self._capture.discard()
        self._end_recording("Recording stopped because the document or layer changed.")

    def _select_document(self) -> None:
        try:
            document_id = self._host.active_document_id()
            document = None if document_id is None else self._host.document_path(document_id)
            if document_id != self._workspace.document_id and self._source.current:
                self._source.clear()
            message = self._workspace.select(document_id, document)
        except Exception as error:
            self._message_changed(f"Could not restore the last recording: {error}")
            return
        if message is not None:
            self._message_changed(message)

    def _saving_failed(self, recording: Recording, error: BaseException) -> None:
        if self._mode in (ServiceMode.CLOSING, ServiceMode.CLOSED):
            return
        if not self._workspace.owns(recording):
            self._message_changed(f"Could not save the previous recording: {error}")
        elif self._mode is ServiceMode.RECORDING:
            self._message_changed(f"Recording without saving: {error}")
            try:
                self._check_limit()
            except Exception as capture_error:
                self.capture_failed(capture_error)
        else:
            self._message_changed(f"Could not save the recording: {error}")

    def _replay_completed(self) -> None:
        self._replay_ended("Replay complete.")

    def _replay_failed(self, error: Exception) -> None:
        self._replay_ended(f"Replay failed: {error}")

    def _replay_ended(self, message: str) -> None:
        if self._mode in (ServiceMode.CLOSING, ServiceMode.CLOSED):
            self._source.clear()
            self._workspace.retire()
            self._mode = ServiceMode.CLOSED
            return
        self._mode = ServiceMode.IDLE
        self._message_changed(message)
        self._select_document()

    def _message_changed(self, message: str) -> None:
        self._message = message
        self._emit()

    def _emit(self) -> None:
        self._on_change(self.state)
