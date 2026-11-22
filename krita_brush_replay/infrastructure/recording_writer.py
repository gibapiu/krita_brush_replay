"""Own bounded recording writes on one worker thread."""

from __future__ import annotations

from collections import deque
from errno import ENOSPC
from threading import Condition, Event, Thread
from time import monotonic

from krita_brush_replay.app.recording_store import StorageBusyError

WRITE_BYTES = 64 * 1024
PENDING_BYTES = 4 * 1024 * 1024
WAIT_SECONDS = 2.0
STORAGE_BUSY = (
    "Recording storage is not responding. Try again shortly. "
    "If this continues, save your work and restart Krita."
)


class RecordingWriter:
    def __init__(self, path: str) -> None:
        self._path = path
        self._condition = Condition()
        self._pending: deque[bytes] = deque()
        self._outstanding = 0
        self._written = 0
        self._failure_message: str | None = None
        self._aborted = False
        self._finishing = False
        self._ready = Event()
        self._done = Event()
        self._thread: Thread | None = None

    def check(self) -> None:
        if self._failure_message is not None:
            raise OSError(self._failure_message)
        if self._aborted:
            raise OSError("recording storage is closed")

    def reset(self, header: bytes) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise StorageBusyError(STORAGE_BUSY)
        self._pending.clear()
        self._outstanding = self._written = 0
        self._failure_message = None
        self._aborted = self._finishing = False
        self._ready.clear()
        self._done.clear()
        deadline = monotonic() + WAIT_SECONDS
        self._thread = Thread(
            target=self._run, args=(header,), name="brush-replay-writer", daemon=True
        )
        self._thread.start()
        self._wait(self._ready, deadline)

    def append(self, data: bytes, *, deadline: float | None = None) -> None:
        with self._condition:
            while self._outstanding + len(data) > PENDING_BYTES:
                self.check()
                if deadline is None:
                    raise OSError("recording storage cannot keep up")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._timeout()
                self._condition.wait(remaining)
            self.check()
            self._pending.append(data)
            self._outstanding += len(data)
            self._condition.notify()

    def finish(self, data: bytes = b"") -> None:
        deadline = monotonic() + WAIT_SECONDS
        if data:
            self.append(data, deadline=deadline)
        with self._condition:
            self.check()
            self._finishing = True
            self._condition.notify()
        self._wait(self._done, deadline)
        self._thread.join(max(0.0, deadline - monotonic()))
        if self._thread.is_alive():
            self._timeout()

    def _wait(self, done: Event, deadline: float) -> None:
        if not done.wait(max(0.0, deadline - monotonic())):
            self._timeout()
        self.check()

    def _timeout(self) -> None:
        with self._condition:
            self._failure_message = self._failure_message or STORAGE_BUSY
            self.abort()
        raise TimeoutError(self._failure_message)

    def abort(self) -> int:
        with self._condition:
            self._aborted = True
            self._pending.clear()
            self._condition.notify_all()
            return self._written

    def _write(self, stream, data: bytes) -> bool:
        remaining = memoryview(data)
        while remaining:
            with self._condition:
                if self._aborted:
                    return False
            written = stream.write(remaining)
            if not written:
                raise OSError("could not write recording data")
            with self._condition:
                if self._aborted:
                    return False
                self._written += written
            remaining = remaining[written:]
        return True

    def _run(self, header: bytes) -> None:
        try:
            with open(self._path, "wb", buffering=0) as stream:
                if not self._write(stream, header):
                    return
                self._ready.set()
                while True:
                    with self._condition:
                        self._condition.wait_for(
                            lambda: self._pending or self._finishing or self._aborted
                        )
                        if self._aborted or not self._pending:
                            return
                        chunks = [self._pending.popleft()]
                        size = len(chunks[0])
                        while self._pending and size + len(self._pending[0]) <= WRITE_BYTES:
                            chunks.append(self._pending.popleft())
                            size += len(chunks[-1])
                    if not self._write(stream, b"".join(chunks)):
                        return
                    with self._condition:
                        self._outstanding -= size
                        self._condition.notify_all()
        except Exception as error:
            with self._condition:
                message = (
                    "not enough disk space. Free some space and try Record again."
                    if isinstance(error, OSError) and error.errno == ENOSPC
                    else str(error)
                )
                self._failure_message = self._failure_message or message
        finally:
            with self._condition:
                self._pending.clear()
                self._condition.notify_all()
            self._ready.set()
            self._done.set()

    def close(self) -> None:
        self.abort()
        if self._thread is not None:
            self._thread.join(timeout=0.05)
