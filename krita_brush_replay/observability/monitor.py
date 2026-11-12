from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter_ns

from krita_brush_replay.observability.report import DiagnosticLog


class Monitor:
    def __init__(self, *, output: logging.Handler | None = None) -> None:
        self.report = DiagnosticLog()
        self._output = output if output is not None else logging.StreamHandler()
        self._output.setFormatter(self.report.formatter)
        self._logger = logging.Logger("krita_brush_replay.monitor", level=logging.INFO)
        self._logger.propagate = False
        self.output_enabled = False
        self.collection_enabled = False
        self.observation_count = 0
        self.observation_ns = 0
        self.failure_count = 0

    @property
    def enabled(self) -> bool:
        return self.output_enabled or self.collection_enabled

    def set_output(self, enabled: bool) -> None:
        self.output_enabled = enabled
        self._set_handler(self._output, enabled)

    def set_collection(self, enabled: bool) -> None:
        self.collection_enabled = enabled
        self._set_handler(self.report, enabled)

    def _set_handler(self, handler: logging.Handler, enabled: bool) -> None:
        if enabled:
            self._logger.addHandler(handler)
        else:
            self._logger.removeHandler(handler)

    def observe(self, callback: Callable, *args) -> None:
        if not self.enabled:
            return
        started = perf_counter_ns()
        try:
            callback(*args)
        except Exception:
            self.failure_count += 1
        finally:
            self.observation_count += 1
            self.observation_ns += perf_counter_ns() - started

    def log(self, message: str, *args) -> None:
        record = self._logger.makeRecord(
            self._logger.name, logging.INFO, "", 0, message, args, None
        )
        for handler in tuple(self._logger.handlers):
            if record.levelno < handler.level:
                continue
            try:
                handler.handle(record)
            except Exception:
                self.failure_count += 1

    def metrics(self) -> dict[str, object]:
        return {
            "output_enabled": self.output_enabled,
            "collection_enabled": self.collection_enabled,
            "observation_count": self.observation_count,
            "observation_work_ms": round(self.observation_ns / 1_000_000, 3),
            "observation_failures": self.failure_count,
        }
