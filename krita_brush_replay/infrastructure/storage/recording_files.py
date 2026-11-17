
from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from hashlib import sha1
from pathlib import Path
from typing import BinaryIO

from krita_brush_replay.app.diagnostics import IssueReporter, ignore_issue
from krita_brush_replay.infrastructure.storage.exclusive_lock import ExclusiveLock

FORMAT_VERSION = 1
KEPT_PER_FOLDER = 10
ABANDONED_TEMPORARY_AGE_S = 3600
META_SUFFIX = ".json"
JOURNAL_SUFFIX = ".strokes"
OWNER_SUFFIX = ".lock"


def document_folder(root: Path, document: str) -> Path:
    name = sha1(os.path.normcase(os.path.abspath(document)).encode()).hexdigest()[:16]
    return root / name


def metadata_files(folder: Path) -> list[Path]:
    return sorted(folder.glob(f"*{META_SUFFIX}"))


def acquire(path: Path) -> ExclusiveLock:
    ownership = ExclusiveLock.acquire(path)
    if ownership is None:
        raise RuntimeError(f"recording {path.stem} is already in use")
    return ownership


def remove_recording(
    base: Path, ownership: ExclusiveLock, report_issue: IssueReporter = ignore_issue
) -> None:
    base.with_suffix(JOURNAL_SUFFIX).unlink(missing_ok=True)
    base.with_suffix(META_SUFFIX).unlink(missing_ok=True)
    ownership.release()
    try:
        ownership.path.unlink(missing_ok=True)
    except OSError as error:
        report_issue(f"recording-lock-cleanup-failed path={ownership.path}", error)


def prune(folder: Path, report_issue: IssueReporter = ignore_issue) -> None:
    abandoned_before = time.time() - ABANDONED_TEMPORARY_AGE_S
    try:
        for temporary in folder.glob("*.tmp"):
            if temporary.stat().st_mtime < abandoned_before:
                temporary.unlink(missing_ok=True)
        for journal in folder.glob(f"*{JOURNAL_SUFFIX}"):
            meta = journal.with_suffix(META_SUFFIX)
            if meta.exists() or journal.stat().st_mtime >= abandoned_before:
                continue
            ownership = ExclusiveLock.acquire(journal.with_suffix(OWNER_SUFFIX))
            if ownership is None:
                continue
            try:
                if not meta.exists():
                    remove_recording(journal.with_suffix(""), ownership, report_issue)
            finally:
                ownership.release()
        for old_meta in metadata_files(folder)[:-KEPT_PER_FOLDER]:
            ownership = ExclusiveLock.acquire(old_meta.with_suffix(OWNER_SUFFIX))
            if ownership is None:
                continue
            try:
                remove_recording(old_meta.with_suffix(""), ownership, report_issue)
            finally:
                ownership.release()
    except OSError as error:
        report_issue(f"recording-prune-failed folder={folder}", error)


def write_atomically(
    path: Path,
    write: Callable[[BinaryIO], object],
    *,
    replace: Callable[[Path, Path], None] = os.replace,
    report_issue: IssueReporter = ignore_issue,
) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as file:
            write(file)
            file.flush()
            os.fsync(file.fileno())
        replace(Path(temporary), path)
    except BaseException:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError as error:
            report_issue(f"recording-temporary-cleanup-failed path={temporary}", error)
        raise
