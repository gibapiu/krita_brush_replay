
import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path
from random import Random
from threading import Event

import pytest
from support import WORKER, ManagedColor, PausedWrites

from krita_brush_replay.domain.recording import ColorState


def start_process(folder, commands):
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("process_session.py")),
            str(folder),
            commands,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_recording_and_edits_follow_first_save_and_save_as_then_reopen(plugin_factory):
    plugin = plugin_factory(document=None)
    plugin.start()
    plugin.krita.view.setBrushSize(12)
    plugin.stroke(1)
    plugin.save("/art/first.kra")
    plugin.stroke(2)
    plugin.krita.action("edit_undo").triggered.emit()
    plugin.save("/art/final.kra")
    plugin.krita.view.setBrushSize(30)
    plugin.stroke(3)
    plugin.stop()
    plugin.override((1,), "size", 42.0)
    plugin.close()

    restored = plugin_factory(document="/art/final.kra")
    restored.replay()
    assert [paint.points[0].position[0] for paint in restored.painted] == [11, 13]
    assert [paint.settings["size"] for paint in restored.painted] == [12, 42]
    restored.reset((1,), "size")
    restored.replay()
    assert [paint.settings["size"] for paint in restored.painted] == [12, 30]
    assert plugin_factory(document="/art/first.kra").state.stroke_count == 0


def test_switching_documents_and_closing_the_last_document_releases_its_recording(plugin_factory):
    plugin = plugin_factory()
    original = plugin.krita.document
    plugin.start()
    plugin.stroke()
    plugin.stop()
    other = plugin.krita.add_document("/art/dog.kra")
    plugin.select(other)
    assert plugin.state.stroke_count == 0
    plugin.select(original)
    plugin.replay()
    assert len(plugin.painted) == 1
    plugin.select(None)
    plugin.saved()
    assert plugin.state.stroke_count == 0
    reopened = plugin_factory()
    reopened.replay()
    assert len(reopened.painted) == 1


@pytest.mark.parametrize("torn_tail", [False, True], ids=["complete", "half-written-last-stroke"])
def test_a_process_crash_recovers_complete_strokes(plugin_factory, tmp_path, torn_tail):
    process = start_process(tmp_path, "start stroke checkpoint stroke crash")
    try:
        assert process.stdout.readline().strip() == "saved"
        journal = next(tmp_path.rglob("*.strokes"))
        first_end = journal.stat().st_size
        _stdout, stderr = process.communicate("\n", timeout=10)
        assert process.returncode == 23, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    if torn_tail:
        last_end = journal.stat().st_size
        with journal.open("r+b") as file:
            file.truncate(first_end + (last_end - first_end) // 2)

    reopened = plugin_factory()
    reopened.choose_current()
    assert "Recovered" in reopened.state.message
    reopened.replay()
    expected = [11] if torn_tail else [11, 12]
    assert [paint.points[0].position[0] for paint in reopened.painted] == expected
    reopened.close()
    reopened_again = plugin_factory()
    reopened_again.replay()
    assert [paint.points[0].position[0] for paint in reopened_again.painted] == expected


def test_a_crash_during_compaction_recovers_the_drawing_after_undo(plugin_factory, tmp_path):
    process = start_process(tmp_path, "start stroke stroke undo crash_during_stop")
    _stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 23, stderr
    reopened = plugin_factory()
    reopened.replay()
    assert [paint.points[0].position[0] for paint in reopened.painted] == [11]
    reopened.close()
    restored = plugin_factory()
    restored.replay()
    assert [paint.points[0].position[0] for paint in restored.painted] == [11]


def test_all_recorded_and_edited_brush_settings_survive_reopening(plugin_factory):
    plugin = plugin_factory()
    view, resources = plugin.krita.view, plugin.krita.presets
    plugin.start()
    view.setCurrentBrushPreset(resources.ink)
    view.setBrushSize(17.25)
    view.opacity, view.flow, view.rotation = 0.4, 0.6, 32
    view.foreground = ManagedColor("RGBA", "F32", "linear-sRGB")
    view.background = ManagedColor("RGBA", "U16", "Display-P3")
    view.foreground.setComponents([0.1, 0.2, 0.3, 1])
    view.background.setComponents([0.8, 0.7, 0.6, 1])
    view.pattern, view.gradient, view.blending_mode = resources.canvas, resources.mesh, "multiply"
    first = view.snapshot()
    plugin.stroke(tablet=True)
    view.setCurrentBrushPreset(resources.airbrush)
    second = view.snapshot()
    plugin.stroke(3)
    plugin.stop()
    choices = plugin.panel.choices
    changed = {
        "preset": next(ref for ref in choices.presets if ref.name == "Pencil"),
        "foreground": ColorState("RGBA", "U8", "sRGB", (0.2, 0.4, 0.6, 1)),
        "background": ColorState("RGBA", "U8", "sRGB", (0.9, 0.5, 0.1, 1)),
        "size": 41.5,
        "opacity": 0.23,
        "flow": 0.34,
        "rotation": 67.0,
        "pattern": next(ref for ref in choices.patterns if ref.name == "Paper"),
        "gradient": next(ref for ref in choices.gradients if ref.name == "Ramp"),
        "blending_mode": "screen",
    }
    for setting, value in changed.items():
        plugin.override((0,), setting, value)
    plugin.close()

    reopened = plugin_factory()
    before_replay = reopened.krita.view.snapshot()
    reopened.replay()
    expected_edit = {
        **changed, "preset": "Pencil", "pattern": "Paper", "gradient": "Ramp",
        "foreground": ("RGBA", "U8", "sRGB", (0.2, 0.4, 0.6, 1)),
        "background": ("RGBA", "U8", "sRGB", (0.9, 0.5, 0.1, 1)),
    }
    assert [paint.settings for paint in reopened.painted] == [expected_edit, second]
    assert reopened.krita.view.snapshot() == before_replay
    reopened.replay(current=True)
    assert [paint.settings for paint in reopened.painted] == [before_replay, before_replay]
    assert reopened.krita.view.snapshot() == before_replay
    for setting in changed:
        reopened.reset((0,), setting)
    reopened.close()
    originals = plugin_factory()
    originals.replay()
    assert [paint.settings for paint in originals.painted] == [first, second]


def test_a_recording_owned_by_another_process_is_left_alone(plugin_factory, tmp_path):
    process = start_process(tmp_path, "start stroke stroke undo checkpoint redo stop close")
    try:
        assert process.stdout.readline().strip() == "saved"
        opened_while_owned = plugin_factory()
        assert opened_while_owned.state.stroke_count == 0
        _stdout, stderr = process.communicate("\n", timeout=10)
        assert process.returncode == 0, stderr
        reopened = plugin_factory()
        reopened.replay()
        assert [paint.points[0].position[0] for paint in reopened.painted] == [11, 12]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="requires POSIX permissions")
def test_failed_save_as_leaves_the_original_recording_readable(plugin_factory, tmp_path):
    plugin = plugin_factory()
    plugin.start()
    plugin.stroke()
    plugin.stop()
    plugin.saved()
    tmp_path.chmod(0o500)
    try:
        plugin.save("/art/moved.kra")
        plugin.saved()
    finally:
        tmp_path.chmod(0o700)
    plugin.replay()
    assert len(plugin.painted) == 1
    plugin.close()
    assert plugin.qt.warnings
    reopened = plugin_factory()
    reopened.replay()
    assert len(reopened.painted) == 1


def test_unavailable_storage_keeps_edits_in_memory_and_warns_on_close(plugin_factory, tmp_path):
    unavailable = tmp_path / "occupied"
    unavailable.write_text("not a directory")
    plugin = plugin_factory(root=unavailable)
    original = plugin.krita.document
    plugin.start()
    plugin.stroke()
    plugin.stop()
    plugin.saved()
    assert "save" in plugin.state.message.lower()
    plugin.override((0,), "size", 42.0)
    plugin.select(plugin.krita.add_document("/art/dog.kra"))
    plugin.select(original)
    plugin.replay()
    assert plugin.painted[0].settings["size"] == 42
    plugin.close()
    assert plugin.qt.warnings


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
@pytest.mark.parametrize("writing", ["initial-save", "compaction"])
def test_closing_during_replay_and_disk_work_keeps_the_recording(
    plugin_factory, monkeypatch, native, writing,
):
    syncing, finish_write = Event(), Event()

    class ClosingDisk:
        def submit(self, key, action):
            WORKER.submit(key, action)

        def wait_until_done(self, key, timeout_s):
            if timeout_s != 0:
                finish_write.set()
            return WORKER.wait_until_done(key, timeout_s)

    plugin = plugin_factory(native=native, worker=ClosingDisk())
    if writing == "compaction":
        plugin.start()
        plugin.stroke(tablet=True)
        plugin.stroke(3)
        plugin.krita.action("edit_undo").triggered.emit()
        plugin.saved()
    sync = os.fsync

    def held_sync(descriptor):
        syncing.set()
        if not finish_write.wait(5):
            raise TimeoutError("test did not release the held disk write")
        sync(descriptor)

    monkeypatch.setattr(os, "fsync", held_sync)
    try:
        if writing == "initial-save":
            plugin.start()
            plugin.stroke(tablet=True)
        plugin.stop()
        assert syncing.wait(5)
        before = plugin.krita.view.snapshot()
        canvas = plugin.krita.document.canvas
        plugin.replay(wait=False)
        if not native:
            while not canvas.pressed:
                plugin.loop.step()
        plugin.close()
        plugin.saved()
        assert not canvas.pressed
        assert plugin.krita.view.snapshot() == before
        assert plugin.qt.warnings == []
    finally:
        finish_write.set()
    reopened = plugin_factory()
    reopened.replay()
    assert [paint.points[0].position[0] for paint in reopened.painted] == [11]


def test_compaction_keeps_redo_working_and_disk_use_bounded_during_long_editing(
    plugin_factory, tmp_path,
):
    plugin = plugin_factory()
    plugin.start()
    plugin.stroke(1)
    plugin.saved()
    undo = plugin.krita.action("edit_undo").triggered.emit
    redo = plugin.krita.action("edit_redo").triggered.emit
    random = Random(17)
    sizes = []
    for attempt in range(32):
        x = 100 + attempt
        plugin.input("MouseButtonPress", x)
        for index in range(1, 12_000):
            x += random.uniform(-2, 2)
            plugin.input("MouseMove", x, index)
        plugin.input("MouseButtonRelease", x, 12_000)
        for _ in range(4):
            undo()
            redo()
        undo()
        plugin.saved()
        sizes.append(sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file()))

    assert any(after < before / 2 for before, after in zip(sizes, sizes[1:]))
    assert plugin.state.stroke_count == 1
    undo()
    undo()
    redo()
    assert plugin.state.stroke_count == 0
    redo()
    redo()
    assert plugin.state.stroke_count == 2
    plugin.stop()
    plugin.close()
    reopened = plugin_factory()
    reopened.replay()
    assert [paint.points[0].position[0] for paint in reopened.painted] == [11, 141]
    assert [len(paint.points) for paint in reopened.painted] == [2, 12_000]


def test_pending_writes_survive_switching_and_limit_further_recording(plugin_factory):
    disk = PausedWrites()
    plugin = plugin_factory(worker=disk)
    original = plugin.krita.document
    try:
        plugin.start()
        plugin.stroke()
        plugin.stop()
        plugin.override((0,), "size", 42.0)
        plugin.select(plugin.krita.add_document("/art/dog.kra"))
        plugin.select(original)
        plugin.replay()
        assert plugin.painted[0].settings["size"] == 42
        for stroke in range(3):
            plugin.start()
            plugin.input("MouseButtonPress", stroke)
            for sample in range(1, 20_000):
                plugin.input("MouseMove", sample, sample)
            plugin.stop()
        assert "waiting to be saved" in plugin.state.message
    finally:
        disk.resume()
    plugin.close()
    reopened = plugin_factory()
    reopened.replay()
    assert len(reopened.painted) == 1


def test_long_saved_recording_can_grow_without_retaining_every_sample_in_memory(plugin_factory):
    plugin = plugin_factory()
    plugin.start()
    tracemalloc.start()
    try:
        for stroke in range(12):
            plugin.input("MouseButtonPress", stroke)
            for sample in range(1, 5_000):
                plugin.input("MouseMove", sample, sample)
            plugin.input("MouseButtonRelease", 5_000, 5_000)
            plugin.saved()
        retained, _peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert plugin.state.can_stop
    assert plugin.state.sample_count == 60_000
    assert retained < 4_000_000
    plugin.stop()
    plugin.close()
    reopened = plugin_factory()
    reopened.replay()
    assert sum(len(paint.points) for paint in reopened.painted) == 60_000


def test_replacing_recordings_keeps_storage_bounded(plugin_factory, tmp_path):
    plugin = plugin_factory()

    def replace_recordings(start):
        for x in range(start, start + 12):
            plugin.start()
            plugin.stroke(x)
            plugin.stop()
            plugin.saved()
        return sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file())

    initial = replace_recordings(0)
    assert replace_recordings(12) < initial * 1.2
    plugin.close()
    reopened = plugin_factory()
    reopened.replay()
    assert reopened.painted[0].points[0].position[0] == 33


def test_aborted_disk_task_is_reported_and_later_recordings_still_save(
    plugin_factory, monkeypatch, tmp_path, issue_log,
):
    sync = os.fsync
    interrupted = False

    def abort_once(descriptor):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise SystemExit(f"storage task aborted at {tmp_path}")
        sync(descriptor)

    monkeypatch.setattr(os, "fsync", abort_once)
    failed = plugin_factory()
    failed.start()
    failed.stroke()
    failed.stop()
    failed.saved()
    assert "storage task aborted" in failed.state.message
    failed.close()
    warnings = len(failed.qt.warnings)
    assert warnings
    assert issue_log.wait_for()

    later = plugin_factory(document="/art/another.kra")
    later.start()
    later.stroke(3)
    later.stop()
    later.close()
    restored = plugin_factory(document="/art/another.kra")
    restored.replay()
    assert restored.painted[0].points[0].position[0] == 13
    assert len(later.qt.warnings) == warnings


@pytest.mark.parametrize("damage", ["recording", "setting-changes"])
def test_damaged_saved_data_is_reported_and_usable_strokes_remain_available(
    plugin_factory, tmp_path, damage, issue_log,
):
    plugin = plugin_factory()
    for x in (1, 2):
        plugin.start()
        plugin.stroke(x)
        plugin.stop()
        plugin.saved()
    plugin.close()
    latest = max(tmp_path.rglob("*.json"))
    if damage == "recording":
        latest.write_text("{incomplete metadata")
    else:
        metadata = json.loads(latest.read_text())
        metadata["overrides"] = [{"size": -5}]
        latest.write_text(json.dumps(metadata))
    restored = plugin_factory()
    restored.replay()
    assert [paint.points[0].position[0] for paint in restored.painted] == (
        [11] if damage == "recording" else [12]
    )
    assert issue_log.wait_for()
    assert issue_log.records[0].exc_text


def test_retention_cleanup_failure_is_reported_without_invalidating_new_recordings(
    plugin_factory, tmp_path, monkeypatch, issue_log,
):
    plugin = plugin_factory()
    for x in range(10):
        plugin.start()
        plugin.stroke(x)
        plugin.stop()
        plugin.saved()
    oldest = min(tmp_path.rglob("*.strokes"))
    unlink = Path.unlink

    def cannot_remove_oldest(path, *args, **kwargs):
        if path == oldest:
            raise PermissionError(f"cannot remove {path}")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_remove_oldest)
    plugin.start()
    plugin.stroke(20)
    plugin.stop()
    plugin.close()
    assert plugin.qt.warnings == []
    assert issue_log.wait_for()
    restored = plugin_factory()
    restored.replay()
    assert restored.painted[0].points[0].position[0] == 30
