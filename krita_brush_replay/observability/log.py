
from __future__ import annotations

import logging
from collections.abc import Callable
from queue import Full, Queue
from threading import Lock, Thread
from traceback import TracebackException


class _LogOutput:
    def __init__(self) -> None:
        self._queue: Queue[Callable[[], None]] = Queue(maxsize=64)
        self._lock = Lock()
        self._thread: Thread | None = None

    def submit(self, emit: Callable[[], None]) -> None:
        self._start()
        try:
            self._queue.put_nowait(emit)
        except Full:
            pass

    def _start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = Thread(target=self._run, name="brush-replay-log", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            emit = self._queue.get()
            try:
                emit()
            except BaseException:
                pass


_output = _LogOutput()


def log(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
    error: BaseException | None = None,
) -> None:
    try:
        if not logger.isEnabledFor(level):
            return
        record = logging.LogRecord(logger.name, level, __file__, 0, message, args, None)
        traceback_snapshot = (
            None
            if error is None
            else TracebackException.from_exception(error, lookup_lines=False)
        )

        def emit() -> None:
            if traceback_snapshot is not None:
                record.exc_text = "".join(traceback_snapshot.format())
            logger.handle(record)

        _output.submit(emit)
    except BaseException:
        pass
