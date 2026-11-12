from __future__ import annotations

import json
import logging
import platform
import sys
import time
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

DIAGNOSTIC_BUILD = "tablet-pressure-capture-v2"
MAX_LOG_RECORDS = 2_000
MAX_RECORD_CHARS = 4_000


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
