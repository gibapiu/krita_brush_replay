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


def test_closing_during_replay_keeps_its_input_alive_until_completion(service_factory):
    """Closing the docker lets an ongoing replay finish reading its input."""

    from tests.support.recording import ReplayHost, draw

    class PendingHost(ReplayHost):
        def replay(self, session, on_complete, on_error, *, use_original_settings):
            self.outputs.append(session)
            self.complete = on_complete

    host = PendingHost()
    service = service_factory(host)
    service.start_recording()
    draw(service, 7)
    service.stop_recording()
    service.replay()
    service.close()
    assert host.outputs[0].strokes[0].samples[0].x == 7
    host.complete()


@pytest.mark.qt
@pytest.mark.parametrize("failure", ["write", "reader-open"])
def test_unavailable_storage_leaves_the_docker_usable(failure, workbench, recording_io):
    """A failed start reports the storage problem and allows another recording."""

    bench = workbench
    fault = "error" if failure == "write" else "reader_open_error"
    setattr(recording_io, fault, "temporary storage unavailable")
    bench.click("Record new session")
    assert bench.message_contains("temporary storage unavailable")
    assert bench.button("Record new session").isEnabled()
    assert not bench.button("Stop").isEnabled()
    assert not bench.button("Replay").isEnabled()
    setattr(recording_io, fault, None)
    bench.record()
    bench.replay()
    assert bench.message_contains("complete")


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_slow_storage_preserves_recording_through_its_lifecycle(
    operation, service_factory, recording_io
):
    """Slow file writes do not turn successful operations into recording failures."""

    from krita_brush_replay.app.service import ServiceMode
    from tests.support.recording import ReplayHost, draw

    host = ReplayHost()
    service = service_factory(host)
    if operation == "start":
        recording_io.delay = 0.2
    service.start_recording()
    assert service.state.mode is ServiceMode.RECORDING
    draw(service, 7)
    if operation == "stop":
        recording_io.delay = 0.2
    service.stop_recording()
    assert service.state.can_replay
    service.replay()
    assert service.state.mode is ServiceMode.IDLE
    assert len(host.outputs) == 1
    assert host.outputs[0].strokes[0].samples[0].x == 7
    assert host.outputs[0].strokes[0].brush_state.size == 12.345


@pytest.mark.parametrize("operation", ["start", "close"])
def test_unresponsive_storage_has_a_bounded_wait_and_a_clear_retry_message(
    operation, service_factory, recording_io
):
    """Hung writes cannot hold Record or docker close indefinitely."""

    from time import monotonic

    from tests.support.recording import (
        ReplayHost,
        draw,
        draw_samples,
        record_when_available,
        varied_samples,
    )

    host = ReplayHost()
    service = service_factory(host)
    if operation == "close":
        service.start_recording()
    recording_io.blocked.set()
    try:
        if operation == "close":
            draw_samples(service, varied_samples(5_000))
            assert recording_io.entered.wait(1)
        started = monotonic()
        if operation == "start":
            service.start_recording()
            assert monotonic() - started < 3
            assert not service.state.can_stop
            assert "storage is not responding" in service.state.message
            assert "save your work and restart Krita" in service.state.message
            service.start_recording()
            assert "Try again shortly" in service.state.message
            assert not service.state.can_stop
        else:
            service.close()
            assert monotonic() - started < 0.5
    finally:
        recording_io.release.set()
    if operation == "start":
        record_when_available(service)
        draw(service, 7)
        service.stop_recording()
        service.replay()
        assert [stroke.samples[0].x for stroke in host.outputs[-1].strokes] == [7]


def test_a_temporary_stall_preserves_short_and_long_strokes(service_factory, recording_io):
    """A delayed writer retains input and Stop waits for the remaining writes."""

    from threading import Event, Thread

    from krita_brush_replay.app.service import ServiceMode
    from tests.support.recording import ReplayHost, draw, draw_samples, varied_samples

    host = ReplayHost()
    service = service_factory(host)
    service.start_recording()
    recording_io.blocked.set()
    points = tuple(varied_samples(5_000))
    draw_samples(service, points)
    for index in range(2_000):
        draw(service, index)
    assert recording_io.entered.wait(1)
    assert service.state.mode is ServiceMode.RECORDING
    stopped = Event()
    stop_started = Event()

    def stop():
        stop_started.set()
        try:
            service.stop_recording()
        finally:
            stopped.set()

    stopper = Thread(target=stop)
    stopper.start()
    try:
        assert stop_started.wait(1)
        assert not stopped.wait(0.05)
    finally:
        recording_io.release.set()
        stopper.join(3)
    assert stopped.is_set()
    assert service.state.can_replay
    service.replay()
    assert tuple(host.outputs[-1].strokes[0].samples) == points
    assert [stroke.samples[0].x for stroke in host.outputs[-1].strokes[1:]] == list(range(2_000))


def test_cleanup_errors_do_not_escape_close_or_prevent_a_new_plugin_instance(
    service_factory, monkeypatch
):
    """A locked temporary file cannot break docker destruction."""

    import os

    from tests.support.recording import ReplayHost, draw

    service = service_factory(ReplayHost())
    service.start_recording()
    draw(service, 1)
    service.stop_recording()

    def locked(*args, **kwargs):
        raise PermissionError("temporary recording is locked")

    with monkeypatch.context() as fault:
        fault.setattr(os, "unlink", locked)
        service.close()
        service.close()
    host = ReplayHost()
    other = service_factory(host)
    other.start_recording()
    draw(other, 2)
    other.stop_recording()
    other.replay()
    assert [stroke.samples[0].x for stroke in host.outputs[-1].strokes] == [2]
