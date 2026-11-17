
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.domain.recording import (
    HistoryStep,
    Stroke,
    compacted_history,
    retained_strokes,
)
from krita_brush_replay.infrastructure.storage.exclusive_lock import ExclusiveLock
from krita_brush_replay.infrastructure.storage.recording_files import (
    FORMAT_VERSION,
    JOURNAL_SUFFIX,
    META_SUFFIX,
    OWNER_SUFFIX,
    acquire,
    prune,
    remove_recording,
    write_atomically,
)
from krita_brush_replay.infrastructure.storage.recording_format import Span, encode_entry, read_entries
from krita_brush_replay.infrastructure.storage.recording_samples import SampleStorage, SavedSamples

COMPACTION_SLACK_BYTES = 1 << 20


@dataclass(slots=True)
class JournalFrame:
    samples: SavedSamples
    span: Span


@dataclass(frozen=True, slots=True)
class JournalSnapshot:

    metadata: dict
    strokes: tuple[Stroke, ...]
    frames: list[JournalFrame | HistoryStep]
    size: int

    @classmethod
    def read(cls, path: Path, samples: SampleStorage) -> JournalSnapshot:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        journal = path.with_suffix(JOURNAL_SUFFIX)
        if metadata.get("format") != FORMAT_VERSION:
            raise ValueError("Unsupported recording format.")
        frames_by_samples: dict[object, JournalFrame] = {}

        def reference(span: Span, count: int) -> SavedSamples:
            value = samples.saved(span, count)
            frames_by_samples[value] = JournalFrame(value, span)
            return value

        with journal.open("rb") as source:
            entries = read_entries(source, reference)
            size = source.seek(0, os.SEEK_END)
        frames = [
            frames_by_samples[entry.samples] if isinstance(entry, Stroke) else entry
            for entry in entries
        ]
        return cls(metadata, retained_strokes(entries), frames, size)


@dataclass(frozen=True, slots=True)
class JournalUpdate:
    entries: tuple[Stroke | HistoryStep, ...]
    metadata: Mapping[str, object]
    target: Path | None
    metadata_changed: bool
    compact: bool


class RecordingJournal:

    def __init__(
        self,
        name: str,
        samples: SampleStorage,
        report_issue: IssueReporter = ignore_issue,
        *,
        directory: Path | None = None,
        ownership: ExclusiveLock | None = None,
        snapshot: JournalSnapshot | None = None,
    ) -> None:
        self._name = name
        self._samples = samples
        self._report_issue = report_issue
        self._directory = directory
        self._ownership = ownership
        self._frames = [] if snapshot is None else list(snapshot.frames)
        self._size = 0 if snapshot is None else snapshot.size
        self._next_compaction_check = COMPACTION_SLACK_BYTES

    def apply(self, update: JournalUpdate) -> None:
        metadata = json.dumps(update.metadata).encode()
        metadata_changed = update.metadata_changed
        if update.target is not None:
            moving = self._ownership is not None and update.target != self._directory
            self._move_to(update.target, metadata)
            if moving:
                metadata_changed = False
        if self._directory is None:
            return
        if update.entries:
            if self._ownership is None:
                self._own(metadata)
                metadata_changed = False
            self._append(update.entries)
        if self._ownership is None:
            return
        if update.compact:
            self._keep_only_retained()
        else:
            self._compact_if_bloated()
        if metadata_changed and self._ownership is not None:
            self._write_meta(metadata)
        if update.compact:
            prune(self._directory, self._report_issue)

    def release(self) -> None:
        ownership, self._ownership = self._ownership, None
        if ownership is not None:
            ownership.release()

    def _path(self, suffix: str) -> Path:
        if self._directory is None:
            raise RuntimeError("The journal has no committed directory.")
        return self._directory / f"{self._name}{suffix}"

    def _own(self, metadata: bytes) -> None:
        self._path(JOURNAL_SUFFIX).parent.mkdir(parents=True, exist_ok=True)
        self._ownership = acquire(self._path(OWNER_SUFFIX))
        self._write_meta(metadata)

    def _write_meta(self, metadata: bytes) -> None:
        write_atomically(
            self._path(META_SUFFIX), lambda file: file.write(metadata),
            report_issue=self._report_issue,
        )

    def _move_to(self, directory: Path, metadata: bytes) -> None:
        if self._ownership is None or directory == self._directory:
            self._directory = directory
            return
        directory.mkdir(parents=True, exist_ok=True)
        moved = acquire(directory / f"{self._name}{OWNER_SUFFIX}")
        source = self._path(JOURNAL_SUFFIX)
        destination = directory / source.name

        def copy_journal(target) -> None:
            with source.open("rb") as journal:
                shutil.copyfileobj(journal, target, length=COMPACTION_SLACK_BYTES)

        try:
            write_atomically(destination, copy_journal, report_issue=self._report_issue)
            write_atomically(
                destination.with_suffix(META_SUFFIX), lambda file: file.write(metadata),
                report_issue=self._report_issue,
            )
        except BaseException:
            for path in (destination, destination.with_suffix(META_SUFFIX)):
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    self._report_issue(f"recording-move-cleanup-failed path={path}", error)
            moved.release()
            raise
        self._samples.publish(destination)
        previous, self._ownership = self._ownership, moved
        self._directory = directory
        try:
            remove_recording(source.with_suffix(""), previous, self._report_issue)
        except OSError as error:
            self._report_issue(f"recording-source-cleanup-failed path={source}", error)
        finally:
            previous.release()

    def _append(self, entries: tuple[Stroke | HistoryStep, ...]) -> None:
        settled: list[tuple[SavedSamples, Span]] = []
        path = self._path(JOURNAL_SUFFIX)
        with path.open("ab") as journal:
            for entry in entries:
                if isinstance(entry, HistoryStep):
                    journal.write(encode_entry(entry))
                    self._frames.append(entry)
                    continue
                samples = entry.samples
                if not isinstance(samples, SavedSamples):
                    raise TypeError("Journal strokes require storage-backed samples.")
                start = journal.tell()
                journal.write(encode_entry(entry))
                span = start, journal.tell() - start
                settled.append((samples, span))
                self._frames.append(JournalFrame(samples, span))
            self._size = journal.tell()
        self._samples.publish(path, settled)

    def _keep_only_retained(self) -> None:
        if self._ownership is None:
            raise RuntimeError("Compacting a journal requires its file lease.")
        retained = retained_strokes(self._frames)
        if not retained:
            remove_recording(
                self._path(JOURNAL_SUFFIX).with_suffix(""), self._ownership, self._report_issue
            )
            self._ownership = None
            self._frames, self._size = [], 0
        elif len(retained) < len(self._frames):
            self._rewrite(list(retained))

    def _compact_if_bloated(self) -> None:
        if self._size < self._next_compaction_check:
            return
        self._next_compaction_check = self._size + COMPACTION_SLACK_BYTES
        entries = compacted_history(self._frames)
        live_bytes = sum(entry.span[1] for entry in entries if isinstance(entry, JournalFrame))
        if self._size > 2 * live_bytes + COMPACTION_SLACK_BYTES:
            self._rewrite(entries)

    def _rewrite(self, entries: list[JournalFrame | HistoryStep]) -> None:
        moved: list[tuple[JournalFrame, Span]] = []
        path = self._path(JOURNAL_SUFFIX)

        def copy_entries(target) -> None:
            with path.open("rb") as source:
                for entry in entries:
                    if isinstance(entry, HistoryStep):
                        target.write(encode_entry(entry))
                        continue
                    start, length = entry.span
                    source.seek(start)
                    moved.append((entry, (target.tell(), length)))
                    target.write(source.read(length))

        def publish(temporary: Path, destination: Path) -> None:
            self._samples.publish(
                destination, ((frame.samples, span) for frame, span in moved),
                replacement=temporary,
            )

        write_atomically(path, copy_entries, replace=publish, report_issue=self._report_issue)
        for frame, span in moved:
            frame.span = span
        self._frames = entries
        self._size = path.stat().st_size
        self._next_compaction_check = self._size + COMPACTION_SLACK_BYTES
