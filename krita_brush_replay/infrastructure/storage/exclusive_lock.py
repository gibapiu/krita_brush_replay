
from __future__ import annotations

import os
from pathlib import Path

try:
    import fcntl
except ImportError:
    import msvcrt

    def _lock(descriptor: int) -> None:
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

else:

    def _lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


class ExclusiveLock:
    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self._descriptor: int | None = descriptor

    @classmethod
    def acquire(cls, path: Path) -> ExclusiveLock | None:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT)
        try:
            _lock(descriptor)
        except OSError:
            os.close(descriptor)
            return None
        return cls(path, descriptor)

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            os.close(descriptor)
