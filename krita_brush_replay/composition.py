
from __future__ import annotations

from pathlib import Path

from krita_brush_replay.app.recording_workspace import RecordingWorkspace
from krita_brush_replay.app.recording_library import RecordingBrowser
from krita_brush_replay.app.service import BrushReplayService
from krita_brush_replay.infrastructure.storage.background_writer import SerialWorker
from krita_brush_replay.infrastructure.host.krita_host import KritaHost
from krita_brush_replay.infrastructure.host.krita_replay import KritaReplayer
from krita_brush_replay.infrastructure.host.qt_facade import QtCore, qt_enum
from krita_brush_replay.infrastructure.storage.recording_archive import RecordingArchive
from krita_brush_replay.infrastructure.storage.recording_library import FileRecordingLibrary
from krita_brush_replay.infrastructure.storage.sample_budget import SampleBudget
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.errors import callback_failed, report_issue
from krita_brush_replay.observability.persistence import (
    ObservedArchive,
    ObservedWorker,
)
from krita_brush_replay.observability.replay import ObservedReplayer
from krita_brush_replay.runtime import BrushReplayRuntime

_worker = ObservedWorker(SerialWorker())
_library_worker = ObservedWorker(SerialWorker())
_samples = SampleBudget()


def _create_service(host, deliver, on_change, on_library_change) -> BrushReplayService:
    location = qt_enum(QtCore.QStandardPaths, "StandardLocation", "AppDataLocation")
    folder = Path(QtCore.QStandardPaths.writableLocation(location)) / "brush_replay"
    archive = ObservedArchive(RecordingArchive(folder, deliver, _worker, _samples, report_issue))
    library = RecordingBrowser(
        FileRecordingLibrary(folder, _library_worker, _worker, _samples, deliver, report_issue),
        on_library_change,
    )
    return BrushReplayService(host, RecordingWorkspace(archive), on_change, library)


def _create_replayer(*args, **kwargs) -> ObservedReplayer:
    return ObservedReplayer(KritaReplayer(*args, **kwargs))


def compose_runtime(panel, application) -> BrushReplayRuntime:
    return BrushReplayRuntime(
        panel,
        service_factory=_create_service,
        callback_error=callback_failed,
        krita_instance=application,
        host_factory=lambda application: KritaHost(
            application, replayer_factory=_create_replayer, report_issue=report_issue
        ),
        input_filter_factory=ObservedInputFilter,
    )
