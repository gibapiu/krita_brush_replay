"""Bounded, shareable diagnostics for input capture and stroke replay."""

from __future__ import annotations

import json
import logging
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from krita_brush_replay.domain.recording import BrushSample

DIAGNOSTIC_BUILD = "tablet-pressure-capture-v1"
MAX_LOG_RECORDS = 2_000
MAX_RECORD_CHARS = 4_000
logger = logging.getLogger("krita_brush_replay")


class DiagnosticLog(logging.Handler):
    def __init__(self, capacity: int = MAX_LOG_RECORDS) -> None:
        super().__init__(logging.INFO)
        self._records: deque[str] = deque(maxlen=capacity)
        self._dropped = 0
        self.report_id = str(uuid4())
        formatter = logging.Formatter(
            "[Brush Replay] %(asctime)sZ %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if len(self._records) == self._records.maxlen:
            self._dropped += 1
        if len(message) > MAX_RECORD_CHARS:
            message = message[:MAX_RECORD_CHARS] + " [truncated]"
        self._records.append(message)

    def render(self, context: dict[str, object]) -> str:
        self.acquire()
        try:
            records = tuple(self._records)
            dropped = self._dropped
        finally:
            self.release()
        metadata = {
            "report_id": self.report_id,
            "diagnostic_build": DIAGNOSTIC_BUILD,
            "exported_utc": datetime.now(timezone.utc).isoformat(),
            "os": f"{platform.system()} {platform.release()} {platform.machine()}",
            "python": sys.version.split()[0],
            "older_log_records_discarded": dropped,
            **context,
        }
        return (
            "Brush Replay diagnostic report\n\n"
            "Tester notes (please fill in):\n"
            "Tablet model / driver: \n"
            "Steps to reproduce: \n"
            "Expected pressure: \n"
            "Actual pressure: \n\n"
            + json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n\nActivity log (oldest retained record first):\n"
            + "\n".join(records)
            + "\n"
        )


def configure_diagnostics() -> DiagnosticLog:
    """Configure only this plugin's logger, once across all docker instances."""
    for handler in logger.handlers:
        if isinstance(handler, DiagnosticLog):
            return handler
    report = DiagnosticLog()
    console = logging.StreamHandler()
    console.setFormatter(report.formatter)
    logger.addHandler(report)
    logger.addHandler(console)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("diagnostics-start build=%s report_id=%s", DIAGNOSTIC_BUILD, report.report_id)
    return report


@dataclass
class PressureTrace:
    """Constant-memory pressure and timing summary; no per-move console output."""

    count: int = 0
    minimum: float = 1.0
    maximum: float = 0.0
    first: float = 0.0
    last: float = 0.0
    started_ms: int = 0
    ended_ms: int = 0
    head: list[float] = field(default_factory=list)

    def observe(self, sample: BrushSample) -> None:
        if self.count == 0:
            self.first = sample.pressure
            self.started_ms = sample.timestamp_ms
        self.count += 1
        self.minimum = min(self.minimum, sample.pressure)
        self.maximum = max(self.maximum, sample.pressure)
        self.last = sample.pressure
        self.ended_ms = sample.timestamp_ms
        if len(self.head) < 8:
            self.head.append(round(sample.pressure, 5))

    def __str__(self) -> str:
        if not self.count:
            return "samples=0"
        return (
            f"samples={self.count} pressure_min={self.minimum:.5f} "
            f"pressure_max={self.maximum:.5f} pressure_first={self.first:.5f} "
            f"pressure_last={self.last:.5f} pressure_head={self.head} "
            f"duration_ms={max(0, self.ended_ms - self.started_ms)}"
        )
