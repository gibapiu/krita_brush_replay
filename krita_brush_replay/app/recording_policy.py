
from dataclasses import dataclass

MAX_UNSAVED_SAMPLES = 50_000
MAX_SAVED_SAMPLES = 1_000_000


@dataclass(frozen=True, slots=True)
class StorageState:
    document: str | None
    failure: str | None
    pending_samples: int

    @property
    def saving(self) -> bool:
        return self.document is not None and self.failure is None


@dataclass(frozen=True, slots=True)
class SampleLimit:
    samples: int
    applies_to: str = ""

    def stop_reason(self, recorded: int, drawing: int) -> str | None:
        if recorded + drawing < self.samples:
            return None
        return f"Recording stopped at {self}."

    def warning(self, before: int, after: int) -> str | None:
        if before < self.samples * 4 // 5 <= after:
            return f"Recording has passed 80% of {self}."
        return None

    def __str__(self) -> str:
        return f"its limit of {self.samples:,} samples{self.applies_to}"


UNSAVED_LIMIT = SampleLimit(MAX_UNSAVED_SAMPLES, " for unsaved recordings")
SAVED_LIMIT = SampleLimit(MAX_SAVED_SAMPLES)


def recording_status(storage: StorageState) -> str:
    if storage.saving:
        return "Recording freehand brush strokes…"
    if storage.failure is not None:
        return f"Recording without saving: {storage.failure}"
    return "Recording without saving: save the document to keep its recordings"


def stop_reason(storage: StorageState, recorded: int, drawing: int) -> str | None:
    if storage.saving and storage.pending_samples + drawing >= MAX_UNSAVED_SAMPLES:
        return f"Recording stopped: {MAX_UNSAVED_SAMPLES:,} samples were waiting to be saved."
    limit = SAVED_LIMIT if storage.saving else UNSAVED_LIMIT
    return limit.stop_reason(recorded, drawing)


def warning(storage: StorageState, before: int, after: int) -> str | None:
    limit = SAVED_LIMIT if storage.saving else UNSAVED_LIMIT
    return limit.warning(before, after)
