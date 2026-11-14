import logging

import pytest
from input_support import FakeHost, TabletEvent, UnmarkedMouseEvent

from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.input_events import event_type
from krita_brush_replay.infrastructure.qt_facade import QtGui
from krita_brush_replay.observability.capture import ObservedInputFilter


@pytest.fixture
def input_types(monkeypatch):
    monkeypatch.setattr(QtGui, "QTabletEvent", TabletEvent)
    monkeypatch.setattr(QtGui, "QMouseEvent", UnmarkedMouseEvent)


@pytest.mark.parametrize("level", [logging.WARNING, logging.DEBUG])
def test_logging_preserves_samples_without_consuming_events(input_types, caplog, level) -> None:
    caplog.set_level(level, logger="krita_brush_replay")
    canvas = object()
    samples, errors = [], []
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: samples.append(args), errors.append
    )
    for name, pressure, timestamp in (
        ("TabletPress", 0.2, 10),
        ("TabletMove", 0.5, 20),
        ("TabletMove", 0.8, 30),
    ):
        assert not observer.eventFilter(canvas, TabletEvent(name, pressure, timestamp))
        mouse = UnmarkedMouseEvent(event_type("MouseButtonPress"), 1.0, 4.0, timestamp)
        assert not observer.eventFilter(canvas, mouse)
    assert not observer.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 40))

    assert errors == []
    assert [phase for phase, _sample in samples] == [
        InputPhase.PRESS,
        InputPhase.MOVE,
        InputPhase.MOVE,
        InputPhase.RELEASE,
    ]
    assert [sample.pressure for _phase, sample in samples if sample] == [0.2, 0.5, 0.8]
    assert caplog.messages == (
        ["capture phase=PRESS", "capture phase=RELEASE"] if level == logging.DEBUG else []
    )


def test_broken_log_handler_does_not_drop_samples(input_types, monkeypatch) -> None:
    from krita_brush_replay.observability.capture import logger

    def broken_log(*args, **kwargs):
        raise OSError("output unavailable")

    monkeypatch.setattr(logger, "log", broken_log)
    canvas = object()
    samples, errors = [], []
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: samples.append(args), errors.append
    )

    observer.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))
    observer.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 20))

    assert len(samples) == 2
    assert errors == []
