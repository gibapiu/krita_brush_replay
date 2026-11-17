
import logging
from threading import Event

import pytest

from krita_brush_replay.domain.recording import InputPhase


def test_a_slow_broken_log_sink_cannot_change_recording_saving_or_replay(plugin_factory):
    entered, release, finished = Event(), Event(), Event()

    class UnreliableOutput(logging.Handler):
        def emit(self, record):
            entered.set()
            try:
                assert release.wait(5)
                raise OSError("log output unavailable")
            finally:
                finished.set()

    logger = logging.getLogger("krita_brush_replay")
    previous_level = logger.level
    handler = UnreliableOutput()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        plugin = plugin_factory()
        plugin.start()
        plugin.stroke()
        assert entered.wait(5)
        plugin.stop()
        plugin.replay()
        assert len(plugin.painted) == 1
        plugin.close()
        reopened = plugin_factory()
        reopened.replay()
        assert len(reopened.painted) == 1
    finally:
        release.set()
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
    assert finished.wait(5)


@pytest.mark.parametrize("boundary", ["capture-callback", "synthetic-release"])
def test_callback_and_cleanup_failures_are_diagnosed_without_escaping_into_the_host(
    plugin_factory, tmp_path, monkeypatch, boundary, issue_log,
):
    plugin = plugin_factory(native=boundary == "capture-callback")
    plugin.start()
    if boundary == "capture-callback":
        def unavailable_panel(state):
            raise RuntimeError(f"panel unavailable for {tmp_path}")

        monkeypatch.setattr(plugin.panel, "apply_state", unavailable_panel)
        plugin.stroke()
    else:
        plugin.stroke()
        plugin.stop()
        before = plugin.krita.view.snapshot()
        plugin.replay(wait=False)
        while not plugin.krita.document.canvas.pressed:
            plugin.loop.step()
        post = plugin.loop.post

        def unavailable_release(receiver, event, on_delivered, on_error):
            if isinstance(event, tuple) and event[0] is InputPhase.RELEASE:
                raise RuntimeError(f"release unavailable for {tmp_path}")
            post(receiver, event, on_delivered, on_error)

        monkeypatch.setattr(plugin.loop, "post", unavailable_release)
        plugin.select(plugin.krita.add_document("/art/other.kra"))
        plugin.loop.run()
        assert plugin.painted == []
        assert plugin.krita.view.snapshot() == before
    assert issue_log.wait_for(lambda record: record.levelno == logging.WARNING)
    plugin.close()
