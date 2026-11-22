from __future__ import annotations

import pytest


@pytest.fixture
def workbench(qapp, monkeypatch):
    from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum
    from tests.support.krita import Krita
    from tests.support.workbench import Workbench

    krita = Krita(monkeypatch)
    bench = Workbench(krita)
    qapp.processEvents()
    yield bench
    bench.runtime.close()
    bench.panel.close()
    bench.panel.deleteLater()
    krita.window.close()
    krita.window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, qt_enum(QtCore.QEvent, "Type", "DeferredDelete"))
    qapp.processEvents()


@pytest.fixture(scope="session")
def qapp(request):
    if request.config.getoption("--qt-api") == "none":
        raise pytest.UsageError("A real QApplication requires --qt-api=pyqt5 or pyqt6")
    from krita_brush_replay.infrastructure.qt_facade import QtWidgets

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def service_factory():
    from krita_brush_replay.app.service import BrushReplayService
    from krita_brush_replay.infrastructure.recording_file import RecordingFile

    services = []

    def create(host):
        service = BrushReplayService(host, store=RecordingFile())
        services.append(service)
        return service

    yield create
    for service in services:
        service.close()


@pytest.fixture
def recording_io(monkeypatch):
    """Inject file failures at the OS-facing file boundary."""

    from errno import ENOSPC
    from threading import Event
    from time import sleep
    from types import SimpleNamespace

    from krita_brush_replay.infrastructure import recording_reader, recording_writer

    controls = SimpleNamespace(
        error=None,
        reader_open_error=None,
        read_error=None,
        delay=0.0,
        remaining=None,
        stall_when_full=False,
        blocked=Event(),
        entered=Event(),
        release=Event(),
    )
    open_file = open

    class Output:
        def __init__(self, stream):
            self._stream = stream

        def write(self, data):
            if controls.delay:
                controls.entered.set()
                sleep(controls.delay)
            if controls.blocked.is_set():
                controls.entered.set()
                controls.release.wait(15)
            if controls.error is not None:
                raise OSError(controls.error)
            if controls.remaining is not None:
                if controls.remaining <= 0:
                    if not controls.stall_when_full:
                        raise OSError(ENOSPC, "No space left on device")
                    controls.entered.set()
                    controls.release.wait(15)
                    controls.remaining = None
                else:
                    data = data[: controls.remaining]
            written = self._stream.write(data)
            if controls.remaining is not None:
                controls.remaining -= written
            return written

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def __enter__(self):
            self._stream.__enter__()
            return self

        def __exit__(self, *args):
            return self._stream.__exit__(*args)

    def open_output(path, mode="r", *args, **kwargs):
        stream = open_file(path, mode, *args, **kwargs)
        writable = "b" in mode and any(flag in mode for flag in "wax+")
        return Output(stream) if writable else stream

    monkeypatch.setattr(recording_writer, "open", open_output, raising=False)

    class Input:
        def __init__(self, stream):
            self._stream = stream

        def read(self, *args):
            if controls.read_error is not None:
                raise OSError(controls.read_error)
            return self._stream.read(*args)

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def open_input(path, mode="r", *args, **kwargs):
        if controls.reader_open_error is not None:
            raise OSError(controls.reader_open_error)
        return Input(open_file(path, mode, *args, **kwargs))

    monkeypatch.setattr(recording_reader, "open", open_input, raising=False)
    yield controls
    controls.release.set()
