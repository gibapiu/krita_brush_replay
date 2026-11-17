
from __future__ import annotations

from collections.abc import Callable
from threading import Condition, Thread
from typing import Protocol

Work = Callable[[], None]
_IDLE = object()


class WorkQueue(Protocol):
    def submit(self, key: object, action: Work) -> None: ...

    def wait_until_done(self, key: object, timeout_s: float | None) -> bool: ...


class SerialWorker:

    def __init__(self) -> None:
        self._pending: dict[object, Work] = {}
        self._running: object = _IDLE
        self._changed = Condition()
        self._thread: Thread | None = None

    def submit(self, key: object, action: Work) -> None:
        with self._changed:
            self._pending[key] = action
            if self._thread is None:
                self._thread = Thread(target=self._run, name="brush-replay-worker", daemon=True)
                self._thread.start()
            self._changed.notify_all()

    def wait_until_done(self, key: object, timeout_s: float | None) -> bool:
        with self._changed:
            return self._changed.wait_for(
                lambda: key not in self._pending and self._running != key, timeout_s
            )

    def _run(self) -> None:
        while True:
            self._execute(*self._next())

    def _next(self) -> tuple[object, Work]:
        with self._changed:
            self._changed.wait_for(lambda: self._pending)
            key = next(iter(self._pending))
            self._running = key
            return key, self._pending.pop(key)

    def _execute(self, key: object, action: Work) -> None:
        try:
            action()
        except BaseException:
            pass
        finally:
            with self._changed:
                self._running = _IDLE
                self._changed.notify_all()
