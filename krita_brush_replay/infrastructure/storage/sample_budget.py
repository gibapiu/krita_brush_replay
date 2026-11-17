
from threading import Lock


class SampleBudget:
    def __init__(self) -> None:
        self._count = 0
        self._lock = Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._count

    def reserve(self, count: int) -> None:
        with self._lock:
            self._count += count

    def release(self, count: int) -> None:
        with self._lock:
            self._count -= count
