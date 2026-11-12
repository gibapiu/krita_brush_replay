import io
import logging

import pytest

from krita_brush_replay.observability.monitor import Monitor


@pytest.mark.parametrize(
    "output,collection", [(False, False), (True, False), (False, True), (True, True)]
)
def test_output_and_collection_are_independent(output, collection) -> None:
    stream = io.StringIO()
    monitor = Monitor(output=logging.StreamHandler(stream))
    monitor.set_output(output)
    monitor.set_collection(collection)

    monitor.observe(monitor.log, "test-pressure value=0.25")

    assert ("test-pressure" in stream.getvalue()) is output
    assert ("test-pressure" in monitor.report.render({})) is collection
    assert monitor.observation_count == int(output or collection)


def test_toggling_sinks_does_not_duplicate_output_or_change_other_loggers() -> None:
    stream = io.StringIO()
    monitor = Monitor(output=logging.StreamHandler(stream))
    root_handlers = logging.getLogger().handlers[:]
    plugin_logger = logging.getLogger("krita_brush_replay")
    plugin_settings = plugin_logger.level, plugin_logger.propagate, plugin_logger.handlers[:]

    for enabled in (True, True, False, True):
        monitor.set_output(enabled)
        monitor.set_collection(enabled)
    monitor.observe(monitor.log, "one-record")

    assert stream.getvalue().count("one-record") == 1
    assert monitor.report.render({}).count("one-record") == 1
    assert logging.getLogger().handlers == root_handlers
    assert (plugin_logger.level, plugin_logger.propagate, plugin_logger.handlers) == plugin_settings


def test_a_broken_output_sink_does_not_prevent_report_collection() -> None:
    class BrokenOutput(logging.Handler):
        def emit(self, record):
            raise OSError("output unavailable")

    monitor = Monitor(output=BrokenOutput())
    monitor.set_output(True)
    monitor.set_collection(True)

    monitor.observe(monitor.log, "retained-record")

    assert "retained-record" in monitor.report.render({})
    assert monitor.failure_count == 1
    assert monitor.observation_count == 1
    assert monitor.observation_ns > 0


def test_disabled_monitor_does_not_execute_observations() -> None:
    monitor = Monitor()
    calls = []

    monitor.observe(lambda: calls.append(True))

    assert calls == []
    assert monitor.metrics()["observation_work_ms"] == 0
