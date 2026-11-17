
from support import LIBRARY_WORKER, PausedWrites, wait_for_writes

from krita_brush_replay.app.service_state import ServiceMode


def record(plugin, x):
    plugin.start()
    plugin.stroke(x)
    plugin.stop()
    return plugin.state.current_recording


def test_choosing_an_older_recording_from_a_closed_document_preserves_the_source(plugin_factory):
    capture = plugin_factory()
    older = record(capture, 1)
    capture.override((0,), "size", 42.0)
    newer = record(capture, 2)
    capture.save("/art/cat-copy.kra")
    moved = capture.state.current_recording
    assert moved.document == "/art/cat-copy.kra"
    assert moved.recording_id == newer.recording_id
    newer = moved
    capture.close()
    other = plugin_factory(document="/art/dog.kra")
    dog = record(other, 9)
    other.close()

    replay = plugin_factory(document="/art/target.kra")
    assert replay.state.source is None
    assert not replay.state.can_replay
    assert {row.key for row in replay.library()} == {older, newer, dog}
    replay.choose(older)
    replay.replay()
    assert replay.painted[0].points[0].position[0] == 11
    assert replay.painted[0].settings["size"] == 42
    replay.select(replay.krita.add_document("/art/another-target.kra"))
    replay.save("/art/renamed-target.kra")
    replay.replay()
    assert replay.state.source == older
    assert replay.painted[0].points[0].position[0] == 11
    assert {row.key for row in replay.library()} == {older, newer, dog}


def test_delete_handles_loaded_closed_document_and_current_recordings(plugin_factory):
    capture = plugin_factory()
    older = record(capture, 1)
    newer = record(capture, 2)
    capture.close()
    other = plugin_factory(document="/art/dog.kra")
    dog = record(other, 9)
    other.close()

    manager = plugin_factory()
    manager.choose(older)
    manager.override((0,), "size", 35.0)
    manager.delete(older, dog)
    assert manager.state.source is None
    assert not manager.state.can_replay
    assert [row.key for row in manager.library()] == [newer]
    current = record(manager, 4)
    manager.delete(current)
    assert manager.state.current_recording is None
    assert manager.state.recorded_strokes == 0
    assert [row.key for row in manager.library()] == [newer]
    manager.close()
    reopened = plugin_factory()
    assert [row.key for row in reopened.library()] == [newer]
    reopened.choose(newer)
    reopened.replay()
    assert reopened.painted[0].points[0].position[0] == 12


def test_in_use_recordings_remain_visible_and_cannot_be_deleted(plugin_factory):
    active = plugin_factory()
    active.start()
    active.stroke()
    active.saved()
    key = active.state.current_recording
    viewer = plugin_factory(document="/art/other.kra")
    assert key in {row.key for row in viewer.library()}
    viewer.choose(key)
    assert viewer.state.source is None
    assert viewer.state.mode is ServiceMode.IDLE
    viewer.delete(key)
    assert viewer.panel.library_state.message
    active.stop()
    active.close()
    viewer.choose(key)
    viewer.replay()
    assert len(viewer.painted) == 1


def test_pending_library_load_does_not_block_ui_or_hold_files_after_close(plugin_factory):
    capture = plugin_factory()
    key = record(capture, 1)
    capture.close()
    queue = PausedWrites(LIBRARY_WORKER)
    viewer = plugin_factory(library_worker=queue)
    try:
        viewer.panel.source_requested.emit(key)
        viewer.qt.processEvents()
        assert viewer.state.mode is ServiceMode.LOADING
        viewer.close()
    finally:
        queue.resume()
    wait_for_writes(LIBRARY_WORKER)
    viewer.qt.processEvents()
    reopened = plugin_factory()
    reopened.choose(key)
    reopened.replay()
    assert len(reopened.painted) == 1
