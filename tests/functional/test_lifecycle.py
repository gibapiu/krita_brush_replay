"""Docker lifecycle, resource availability, and diagnostic isolation."""

import logging

import pytest


@pytest.mark.qt
def test_capture_and_resource_listing_failures_are_visible_and_recoverable(workbench):
    """Capture and resource listing failures are visible and recoverable."""

    bench = workbench
    bench.click("Record new session")
    bench.krita.capture_error = "active brush unavailable"
    bench.stroke()
    assert bench.message_contains("active brush unavailable")
    assert not bench.button("Replay").isEnabled()
    bench.krita.capture_error = None
    bench.krita.resource_error = "resources locked"
    bench.record()
    assert bench.message_contains("resources locked")
    bench.krita.resource_error = None
    bench.record()
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.qt
def test_history_actions_stop_at_stop_and_capture_ends_when_the_docker_closes(workbench, caplog):
    """Stop detaches undo actions; closing a recording docker does not affect the next one."""

    from tests.support.krita import tablet
    from tests.support.workbench import Workbench

    bench = workbench
    bench.click("Record new session")
    bench.stroke()
    bench.stroke(90)
    bench.krita.action("edit_undo").trigger()
    bench.krita.action("edit_redo").trigger()
    bench.krita.action("edit_undo").trigger()
    bench.click("Stop")
    bench.krita.action("edit_undo").trigger()
    bench.replay()
    assert len(bench.krita.document.layer.writes) == 1

    bench.click("Record new session")
    tablet(bench.krita.canvas, "Press")
    bench.runtime.close()
    bench.runtime.close()
    bench.panel.close()
    bench.krita.capture_error = "closed capture must not read brush state"
    tablet(bench.krita.canvas, "Press")
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    bench.krita.capture_error = None
    reopened = Workbench(bench.krita)
    try:
        reopened.record()
        reopened.replay()
        assert len(bench.krita.document.layer.writes) == 2
        assert bench.message_contains("recording")
    finally:
        reopened.runtime.close()
        reopened.panel.close()
        reopened.panel.deleteLater()


@pytest.mark.qt
def test_logging_failures_cannot_interfere_with_capture_or_replay_outcomes(workbench, caplog):
    """Capture and replay report useful diagnostics even when host logging fails."""

    caplog.set_level(logging.DEBUG, logger="krita_brush_replay")
    bench = workbench
    bench.record()
    bench.replay()
    assert any("capture" in record.message for record in caplog.records)
    assert any("replay-finished" in record.message for record in caplog.records)

    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise OSError("output unavailable")

    logger = logging.getLogger("krita_brush_replay")
    handler = BrokenHandler()
    logger.addHandler(handler)
    try:
        bench.record()
        bench.replay()
        assert bench.message_contains("complete")
        bench.krita.document.layer.failure = "painting unavailable"
        bench.replay()
        assert bench.message_contains("painting unavailable")
    finally:
        logger.removeHandler(handler)


@pytest.mark.qt
def test_registered_plugin_opens_a_usable_brush_replay_docker(workbench):
    """Opening the registered docker exposes recording and replay controls."""

    from krita_brush_replay.infrastructure.qt_facade import QtWidgets
    from krita_brush_replay.plugin import register_plugin

    krita = workbench.krita
    assert register_plugin()
    dock = krita.factories[0].kind()
    try:
        assert dock.windowTitle() == "Brush Replay"
        panel = dock.findChild(QtWidgets.QWidget)
        assert panel is not None
        buttons = [button.text() for button in dock.findChildren(QtWidgets.QPushButton)]
        assert "Record new session" in buttons
        assert "Replay" in buttons
    finally:
        dock.deleteLater()
