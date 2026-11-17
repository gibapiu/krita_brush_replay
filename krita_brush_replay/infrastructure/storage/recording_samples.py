
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from threading import Lock
from weakref import WeakKeyDictionary

from krita_brush_replay.domain.recording import BrushSample, PackedSamples, StoredSamples
from krita_brush_replay.infrastructure.storage.recording_format import Span, decode_samples


class SavedSamples(StoredSamples):

    __slots__ = ("_storage", "_count", "__weakref__")

    def __init__(self, storage: SampleStorage, count: int) -> None:
        self._storage = storage
        self._count = count

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index: int | slice):
        return self._storage.read(self)[index]

    def __iter__(self) -> Iterator[BrushSample]:
        return iter(self._storage.read(self))

    def __bytes__(self) -> bytes:
        return bytes(self._storage.read(self))


class SampleStorage:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = Lock()
        self._backing: WeakKeyDictionary[SavedSamples, PackedSamples | Span] = WeakKeyDictionary()
        self._cached: tuple[SavedSamples, PackedSamples] | None = None

    def pending(self, samples: Sequence[BrushSample]) -> SavedSamples:
        if not isinstance(samples, PackedSamples):
            samples = PackedSamples(samples)
        return self._add(samples, len(samples))

    def saved(self, span: Span, count: int) -> SavedSamples:
        return self._add(span, count)

    def _add(self, backing: PackedSamples | Span, count: int) -> SavedSamples:
        reference = SavedSamples(self, count)
        with self._lock:
            self._backing[reference] = backing
        return reference

    def read(self, reference: SavedSamples) -> PackedSamples:
        with self._lock:
            if self._cached is not None and self._cached[0] is reference:
                return self._cached[1]
            backing = self._backing[reference]
            if isinstance(backing, PackedSamples):
                return backing
            if self._path is None:
                raise RuntimeError("Saved samples have no backing file.")
            start, length = backing
            with self._path.open("rb") as source:
                source.seek(start)
                frame = source.read(length)
        loaded = decode_samples(frame)
        with self._lock:
            self._cached = reference, loaded
        return loaded

    def publish(
        self,
        path: Path,
        locations: Iterable[tuple[SavedSamples, Span]] = (),
        *,
        replacement: Path | None = None,
    ) -> None:
        with self._lock:
            if replacement is not None:
                os.replace(replacement, path)
            self._path = path
            for reference, span in locations:
                self._backing[reference] = span
