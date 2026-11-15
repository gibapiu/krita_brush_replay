import logging

from input_support import FakeHost, TabletEvent

from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.qt_facade import QtGui
from krita_brush_replay.observability.capture import ObservedInputFilter


def test_capture_logs_stroke_boundaries_and_passes_samples_through(monkeypatch, caplog) -> None:
    monkeypatch.setattr(QtGui, "QTabletEvent", TabletEvent)
    caplog.set_level(logging.DEBUG, logger="krita_brush_replay")
    canvas = object()
    samples, errors = [], []
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: samples.append(args), errors.append
    )

    for name, pressure, timestamp in (
        ("TabletPress", 0.2, 10),
        ("TabletMove", 0.5, 20),
        ("TabletRelease", 0.0, 30),
    ):
        assert not observer.eventFilter(canvas, TabletEvent(name, pressure, timestamp))

    assert errors == []
    assert [phase for phase, _sample in samples] == [
        InputPhase.PRESS,
        InputPhase.MOVE,
        InputPhase.RELEASE,
    ]
    assert [sample.pressure for _phase, sample in samples if sample] == [0.2, 0.5]
    assert caplog.messages == ["capture phase=PRESS", "capture phase=RELEASE"]
