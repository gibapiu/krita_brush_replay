
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from time import monotonic

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.app.recording_library import DeleteResult, RecordingInfo, RecordingKey
from krita_brush_replay.app.recording_store import FailureListener, Recording, SavedRecording
from krita_brush_replay.infrastructure.storage.background_writer import WorkQueue
from krita_brush_replay.infrastructure.storage.exclusive_lock import ExclusiveLock
from krita_brush_replay.infrastructure.storage.file_recording import FileRecording
from krita_brush_replay.infrastructure.storage.recording_files import (
    FORMAT_VERSION, JOURNAL_SUFFIX, OWNER_SUFFIX, document_folder, remove_recording,
)
from krita_brush_replay.infrastructure.storage.sample_budget import SampleBudget


class FileRecordingLibrary:
    def __init__(
        self, root: Path, worker: WorkQueue, writer: WorkQueue, budget: SampleBudget,
        deliver, report_issue: IssueReporter = ignore_issue,
    ) -> None:
        self._root, self._worker, self._writer = root, worker, writer
        self._budget, self._deliver, self._report_issue = budget, deliver, report_issue
        self._lock = Lock()
        self._owned: set[Recording] = set()
        self._closed = False
        self._failed = Event()
        self._cleanup_key = object()
        self._failure_listener: FailureListener = lambda recording, error: None

    def watch_failures(self, listener: FailureListener) -> None:
        self._failure_listener = listener

    def browse(self, done, failed) -> None:
        def scan():
            settled = Event()
            self._writer.submit(settled, settled.set)
            if not settled.wait(5):
                raise RuntimeError("Still saving. Refresh shortly.")
            entries = []
            for path in self._root.glob("*/*.json"):
                try:
                    metadata = json.loads(path.read_text(encoding="utf-8"))
                    if metadata.get("format") != FORMAT_VERSION:
                        raise ValueError("Unsupported recording format.")
                    document = metadata["document"]
                    if not isinstance(document, str):
                        raise ValueError("Recording has no source document.")
                    if not document or not path.with_suffix(JOURNAL_SUFFIX).is_file():
                        continue
                    created = datetime.strptime(path.stem.rsplit("-", 1)[0], "%Y%m%d-%H%M%S-%f")
                    entries.append(RecordingInfo(
                        RecordingKey(document, path.stem), created.replace(tzinfo=timezone.utc)
                    ))
                except Exception as error:
                    self._report_issue(f"library-recording-skipped path={path}", error)
            return tuple(sorted(entries, key=lambda row: row.created, reverse=True))

        self._submit(scan, done, failed)

    def load(self, key, done, failed) -> None:
        def load():
            path = self._path(key)
            saved = FileRecording.open(
                path, lambda document: document_folder(self._root, document),
                self._writer, self._budget, self._saving_failed, self._report_issue,
            )
            with self._lock:
                self._owned.add(saved.recording)
            return saved

        self._submit(load, done, failed)

    def remove(self, keys, wait_for, done, failed) -> None:
        def remove():
            for recording in wait_for:
                if not recording.wait_until_flushed(5):
                    raise RuntimeError("Recording is still saving. Try again shortly.")
            removed, errors = [], []
            for key in keys:
                if key.document is None:
                    removed.append(key)
                    continue
                path = self._path(key)
                ownership = None
                try:
                    if not path.exists():
                        removed.append(key)
                        continue
                    ownership = ExclusiveLock.acquire(path.with_suffix(OWNER_SUFFIX))
                    if ownership is None:
                        raise RuntimeError("Recording is in use. Try again shortly.")
                    remove_recording(path.with_suffix(""), ownership, self._report_issue)
                    removed.append(key)
                except (OSError, RuntimeError) as error:
                    self._report_issue(f"library-delete-failed path={path}", error)
                    errors.append(str(error))
                finally:
                    if ownership is not None:
                        ownership.release()
            return DeleteResult(tuple(removed), tuple(errors))

        self._submit(remove, done, failed)

    def release(self, recording: Recording) -> None:
        recording.close()
        self._writer.submit(self._cleanup_key, self._collect)

    def close(self, timeout_s: float | None, *, keep: Recording | None = None) -> bool:
        with self._lock:
            self._closed = True
            owned = tuple(self._owned)
        for recording in owned:
            if recording is not keep:
                recording.close()
        deadline = None if timeout_s is None else monotonic() + timeout_s
        saved = True
        for recording in owned:
            remaining = None if deadline is None else max(0, deadline - monotonic())
            if not recording.wait_until_saved(remaining):
                saved = False
        self._collect()
        return saved and not self._failed.is_set()

    def _path(self, key: RecordingKey) -> Path:
        if key.document is None:
            raise ValueError("Save the document to keep this recording.")
        if Path(key.recording_id).name != key.recording_id or "\\" in key.recording_id:
            raise ValueError("Invalid recording.")
        return document_folder(self._root, key.document) / f"{key.recording_id}.json"

    def _submit(self, action, done, failed) -> None:
        def execute():
            self._collect()
            with self._lock:
                if self._closed:
                    return
            try:
                result = action()
            except BaseException as error:
                self._report_issue("recording-library-operation-failed", error)
                self._notify(lambda error=error: failed(error))
                return
            with self._lock:
                closed = self._closed
            if closed:
                if isinstance(result, SavedRecording):
                    self.release(result.recording)
                return
            if not self._notify(lambda: done(result)) and isinstance(result, SavedRecording):
                self.release(result.recording)

        self._worker.submit(object(), execute)

    def _notify(self, callback) -> bool:
        try:
            self._deliver(callback)
            return True
        except Exception as error:
            self._report_issue("library-callback-delivery-failed", error)
            return False

    def _saving_failed(self, recording: Recording, error: BaseException) -> None:
        self._failed.set()
        self._notify(lambda: self._failure_listener(recording, error))

    def _collect(self) -> None:
        with self._lock:
            finished = {
                recording for recording in self._owned
                if recording.released and recording.wait_until_flushed(0)
            }
            self._owned.difference_update(finished)
