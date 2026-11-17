
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from secrets import token_hex
from threading import Lock

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.app.recording_policy import StorageState
from krita_brush_replay.app.recording_library import RecordingKey
from krita_brush_replay.app.recording_store import FailureListener, SavedRecording
from krita_brush_replay.domain.brush_override import BrushStatePatch, StrokeSettings
from krita_brush_replay.domain.recording import (
    ContextRef,
    HistoryStep,
    RecordingSession,
    Stroke,
    compacted_history,
)
from krita_brush_replay.infrastructure.storage.background_writer import WorkQueue
from krita_brush_replay.infrastructure.storage.exclusive_lock import ExclusiveLock
from krita_brush_replay.infrastructure.storage.recording_files import (
    FORMAT_VERSION,
    JOURNAL_SUFFIX,
    OWNER_SUFFIX,
)
from krita_brush_replay.infrastructure.storage.recording_format import decode_overrides, encode_overrides
from krita_brush_replay.infrastructure.storage.recording_journal import (
    JournalSnapshot,
    JournalUpdate,
    RecordingJournal,
)
from krita_brush_replay.infrastructure.storage.recording_samples import SampleStorage
from krita_brush_replay.infrastructure.storage.sample_budget import SampleBudget

Change = Stroke | HistoryStep
FolderOf = Callable[[str], Path]


@dataclass(slots=True)
class _Requests:
    entries: list[Change] = field(default_factory=list)
    target: Path | None = None
    metadata_changed: bool = False
    compact: bool = False
    release: bool = False
    samples: int = 0

    @property
    def empty(self) -> bool:
        return not (self.entries or self.target or self.metadata_changed or self.compact)


class _Lease(Enum):
    ACTIVE = auto()
    PARKED = auto()
    RELEASED = auto()


class FileRecording:
    @classmethod
    def new(
        cls,
        document: str | None,
        origin: ContextRef,
        folder_of: FolderOf,
        worker: WorkQueue,
        budget: SampleBudget,
        report_failure: FailureListener,
        report_issue: IssueReporter = ignore_issue,
    ) -> FileRecording:
        metadata = {
            "format": FORMAT_VERSION,
            "document": document,
            "origin": [origin.document_id, origin.layer_id],
            "finished": False,
        }
        name = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}-{token_hex(2)}"
        recording = cls(name, metadata, folder_of, worker, budget, report_failure, report_issue)
        if document:
            recording.follow(document)
        return recording

    @classmethod
    def open(
        cls,
        path: Path,
        folder_of: FolderOf,
        worker: WorkQueue,
        budget: SampleBudget,
        report_failure: FailureListener,
        report_issue: IssueReporter = ignore_issue,
        *,
        ownership: ExclusiveLock | None = None,
    ) -> SavedRecording:
        ownership = ownership or ExclusiveLock.acquire(path.with_suffix(OWNER_SUFFIX))
        if ownership is None:
            raise RuntimeError("Recording is in use. Try again shortly.")
        try:
            samples = SampleStorage(path.with_suffix(JOURNAL_SUFFIX))
            snapshot = JournalSnapshot.read(path, samples)
            metadata, strokes = snapshot.metadata, snapshot.strokes
            origin = ContextRef(*metadata["origin"])
            recovered = not metadata["finished"]
            if not strokes:
                raise ValueError("Recording contains no complete strokes.")
            try:
                patches = decode_overrides(metadata.get("overrides", [{}] * len(strokes)))
                if len(patches) != len(strokes):
                    raise ValueError("Saved setting changes do not match the stroke count.")
                settings = tuple(
                    StrokeSettings(stroke.brush_state, patch)
                    for stroke, patch in zip(strokes, patches)
                )
            except Exception as error:
                report_issue(f"recording-overrides-reset path={path}", error)
                settings = tuple(StrokeSettings(stroke.brush_state) for stroke in strokes)
            recording = cls(
                path.stem, metadata, folder_of, worker, budget, report_failure, report_issue,
                samples=samples, snapshot=snapshot, directory=path.parent, ownership=ownership,
            )
            if recovered:
                recording.seal(tuple(setting.override for setting in settings))
            return SavedRecording(
                RecordingSession(origin, strokes), settings, recovered, recording
            )
        except BaseException:
            ownership.release()
            raise

    def __init__(
        self,
        name: str,
        metadata: dict,
        folder_of: FolderOf,
        worker: WorkQueue,
        budget: SampleBudget,
        report_failure: FailureListener,
        report_issue: IssueReporter = ignore_issue,
        *,
        samples: SampleStorage | None = None,
        snapshot: JournalSnapshot | None = None,
        directory: Path | None = None,
        ownership: ExclusiveLock | None = None,
    ) -> None:
        self._id = name
        self._metadata = metadata
        self._folder_of = folder_of
        self._worker = worker
        self._budget = budget
        self._report_failure = report_failure
        self._samples = samples if samples is not None else SampleStorage()
        self._journal = RecordingJournal(
            name, self._samples, report_issue,
            directory=directory, ownership=ownership, snapshot=snapshot,
        )
        self._lock = Lock()
        self._requests = _Requests()
        self._target = directory
        self._failure: str | None = None
        self._lease = _Lease.ACTIVE

    @property
    def key(self) -> RecordingKey:
        with self._lock:
            return RecordingKey(self._metadata["document"], self._id)

    @property
    def storage(self) -> StorageState:
        with self._lock:
            return StorageState(self._metadata["document"], self._failure, self._budget.pending)

    @property
    def released(self) -> bool:
        with self._lock:
            return self._lease is _Lease.RELEASED

    def park(self) -> None:
        with self._lock:
            self._lease = _Lease.PARKED
            saving = self._saving_locked()
        if saving:
            self._worker.submit(self, self._flush)

    def claim(self) -> bool:
        with self._lock:
            if self._lease is _Lease.RELEASED:
                return False
            self._lease = _Lease.ACTIVE
            return True

    def keep(self, entry: Change) -> Change:
        if isinstance(entry, Stroke):
            entry = Stroke(self._samples.pending(entry.samples), entry.brush_state)
        with self._lock:
            if self._failure is not None:
                return entry
            self._requests.entries.append(entry)
            saving = self._target is not None
            if saving:
                self._reserve(_sample_count([entry]))
            else:
                self._requests.entries = compacted_history(self._requests.entries)
        if saving:
            self._worker.submit(self, self._flush)
        return entry

    def seal(self, overrides: tuple[BrushStatePatch, ...]) -> None:
        self._change_meta(compact=True, finished=True, overrides=encode_overrides(overrides))

    def keep_overrides(self, overrides: tuple[BrushStatePatch, ...]) -> None:
        self._change_meta(overrides=encode_overrides(overrides))

    def follow(self, document: str) -> None:
        target = self._folder_of(document)
        with self._lock:
            if self._failure is not None or target == self._target:
                return
            unsaved = 0 if self._target else _sample_count(self._requests.entries)
            self._target = target
            self._metadata["document"] = document
            self._requests.target = target
            self._requests.metadata_changed = True
            self._reserve(unsaved)
        self._worker.submit(self, self._flush)

    def close(self) -> None:
        with self._lock:
            if self._lease is _Lease.RELEASED:
                return
            if self._target is None:
                self._requests = _Requests()
            self._requests.release = True
        self._worker.submit(self, self._flush)

    def wait_until_flushed(self, timeout_s: float | None = None) -> bool:
        return self._worker.wait_until_done(self, timeout_s)

    def wait_until_saved(self, timeout_s: float | None = None) -> bool:
        return self.wait_until_flushed(timeout_s) and self.storage.failure is None

    def _saving_locked(self) -> bool:
        return self._target is not None and self._failure is None

    def _reserve(self, samples: int) -> None:
        self._budget.reserve(samples)
        self._requests.samples += samples

    def _change_meta(self, *, compact: bool = False, **changes: object) -> None:
        with self._lock:
            self._metadata.update(changes)
            self._requests.metadata_changed = True
            self._requests.compact = self._requests.compact or compact
            saving = self._target is not None
        if saving:
            self._worker.submit(self, self._flush)

    def _flush(self) -> None:
        with self._lock:
            requests, self._requests = self._requests, _Requests()
            update = JournalUpdate(
                tuple(requests.entries), dict(self._metadata), requests.target,
                requests.metadata_changed, requests.compact,
            )
            failed = self._failure is not None
        error = None
        try:
            if not failed:
                self._journal.apply(update)
        except BaseException as caught:
            error = caught
            with self._lock:
                self._failure = str(caught) or type(caught).__name__
        finally:
            self._budget.release(requests.samples)
            with self._lock:
                parked = self._lease is _Lease.PARKED
                if requests.release or (parked and self._saving_locked() and self._requests.empty):
                    self._journal.release()
                    self._lease = _Lease.RELEASED
        if error is not None:
            try:
                self._report_failure(self, error)
            finally:
                raise error


def _sample_count(entries: list[Change]) -> int:
    return sum(len(entry.samples) for entry in entries if isinstance(entry, Stroke))
