
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.app.recording_store import FailureListener, Recording, SavedRecording
from krita_brush_replay.domain.recording import ContextRef
from krita_brush_replay.infrastructure.storage.background_writer import WorkQueue
from krita_brush_replay.infrastructure.storage.file_recording import FileRecording
from krita_brush_replay.infrastructure.storage.recording_files import document_folder
from krita_brush_replay.infrastructure.storage.sample_budget import SampleBudget

Deliver = Callable[[Callable[[], None]], None]


class RecordingArchive:
    def __init__(
        self, root: Path, deliver: Deliver, worker: WorkQueue, budget: SampleBudget,
        report_issue: IssueReporter = ignore_issue,
    ) -> None:
        self._root = root
        self._deliver = deliver
        self._worker = worker
        self._budget = budget
        self._report_issue = report_issue
        self._failure_listener: FailureListener = _ignore_failure
        self._parked: dict[Path, SavedRecording] = {}
        self._tracked: set[Recording] = set()
        self._retired: set[Recording] = set()
        self._save_failed = Event()

    def watch_failures(self, listener: FailureListener) -> None:
        self._failure_listener = listener

    def create(self, document: str | None, origin: ContextRef) -> Recording:
        self._collect()
        recording = FileRecording.new(
            document, origin, self._folder, self._worker, self._budget, self._report_failure,
            self._report_issue,
        )
        self._tracked.add(recording)
        return recording

    def recall(self, document: str) -> SavedRecording | None:
        self._collect()
        saved = self._parked.pop(self._folder(document), None)
        if saved is not None and saved.recording.claim():
            return saved
        if saved is not None:
            self._retired.add(saved.recording)
        self._collect()
        return None

    def remember(self, saved: SavedRecording) -> None:
        recording = saved.recording
        document = recording.storage.document
        if not document:
            self.retire(recording)
            return
        key = self._folder(document)
        previous = self._parked.get(key)
        if previous is not None and previous.recording is not recording:
            self.retire(previous.recording)
        self._parked[key] = saved
        recording.park()
        self._collect()

    def retire(self, recording: Recording) -> None:
        recording.close()
        self._retired.add(recording)
        self._collect()

    def close(self, timeout_s: float | None = None, *, keep: Recording | None = None) -> bool:
        self._parked.clear()
        for recording in tuple(self._tracked):
            if recording is not keep:
                self.retire(recording)
        deadline = None if timeout_s is None else monotonic() + timeout_s
        saved = True
        for recording in tuple(self._tracked):
            remaining = None if deadline is None else max(0.0, deadline - monotonic())
            if not recording.wait_until_saved(remaining):
                saved = False
        self._collect()
        return saved and not self._save_failed.is_set()

    def _collect(self) -> None:
        for key, saved in tuple(self._parked.items()):
            recording = saved.recording
            if recording.released:
                del self._parked[key]
                self._retired.add(recording)
        for recording in tuple(self._retired):
            if recording.wait_until_flushed(0):
                self._retired.remove(recording)
                self._tracked.discard(recording)

    def _folder(self, document: str) -> Path:
        return document_folder(self._root, document)

    def _report_failure(self, recording: Recording, error: BaseException) -> None:
        self._save_failed.set()
        listener = self._failure_listener
        try:
            self._deliver(lambda: listener(recording, error))
        except Exception as delivery_error:
            self._report_issue("recording-failure-delivery-failed", delivery_error)


def _ignore_failure(recording: Recording, error: BaseException) -> None:
    pass
