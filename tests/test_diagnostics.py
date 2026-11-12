import logging

from krita_brush_replay.domain.recording import BrushSample
from krita_brush_replay.infrastructure import diagnostics
from krita_brush_replay.infrastructure.diagnostics import DiagnosticLog, PressureTrace


def test_report_retains_recent_logs_and_marks_discarded_history() -> None:
    report = DiagnosticLog(capacity=2)
    logger = logging.Logger("report-test", level=logging.INFO)
    logger.addHandler(report)

    logger.info("old activity")
    logger.info("capture preset=%s", "鉛筆")
    logger.error("replay failed")
    text = report.render({"krita_version": "5.4", "stroke_count": 1})

    assert "old activity" not in text
    assert "capture preset=鉛筆" in text
    assert "ERROR replay failed" in text
    assert '"older_log_records_discarded": 1' in text
    assert '"krita_version": "5.4"' in text
    assert f'"report_id": "{report.report_id}"' in text
    assert diagnostics.DIAGNOSTIC_BUILD in text


def test_logging_setup_is_idempotent_and_outputs_info_without_root_configuration(
    monkeypatch, capsys
) -> None:
    logger = logging.Logger("plugin-test")
    monkeypatch.setattr(diagnostics, "logger", logger)

    report = diagnostics.configure_diagnostics()
    assert diagnostics.configure_diagnostics() is report
    logger.info("pressure_min=0.2")

    assert len(logger.handlers) == 2
    assert capsys.readouterr().err.count("pressure_min=0.2") == 1
    assert "pressure_min=0.2" in report.render({})
    assert not logger.propagate


def test_long_error_messages_are_bounded_in_the_report() -> None:
    report = DiagnosticLog()
    logger = logging.Logger("report-test", level=logging.INFO)
    logger.addHandler(report)

    logger.error("x" * 100_000)

    text = report.render({})
    assert "[truncated]" in text
    assert len(text) < 6_000


def test_pressure_trace_retains_extrema_after_its_preview_is_full() -> None:
    trace = PressureTrace()
    for timestamp in range(100):
        trace.observe(BrushSample(0.0, 0.0, 0.5, timestamp))
    trace.observe(BrushSample(0.0, 0.0, 0.1, 100))
    trace.observe(BrushSample(0.0, 0.0, 0.9, 101))

    assert len(trace.head) == 8
    assert trace.count == 102
    assert "pressure_min=0.10000 pressure_max=0.90000" in str(trace)
    assert "pressure_last=0.90000" in str(trace)
    assert "duration_ms=101" in str(trace)
