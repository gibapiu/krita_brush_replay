import io
import logging

import pytest
from input_support import FakeHost, TabletEvent, UnmarkedMouseEvent

from krita_brush_replay.domain.recording import InputPhase
from krita_brush_replay.infrastructure.input_events import event_type
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, qt_enum
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.monitor import Monitor


@pytest.fixture
def input_types(monkeypatch):
    monkeypatch.setattr(QtGui, "QTabletEvent", TabletEvent)
    monkeypatch.setattr(QtGui, "QMouseEvent", UnmarkedMouseEvent)


@pytest.mark.parametrize("enabled", [False, True])
def test_monitoring_preserves_tablet_samples_and_does_not_consume_events(
    input_types, enabled
) -> None:
    monitor = Monitor()
    monitor.set_collection(enabled)
    canvas = object()
    samples, errors = [], []
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: samples.append(args), errors.append, monitor=monitor
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
    text = monitor.report.render({})
    assert ("samples=3 pressure_min=0.20000 pressure_max=0.80000" in text) is enabled
    assert (monitor.observation_count > 0) is enabled


def test_capture_summarizes_ignored_events_without_logging_every_move(input_types) -> None:
    monitor = Monitor()
    monitor.set_collection(True)
    canvas = object()
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: None, lambda error: None, monitor=monitor
    )

    observer.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))
    for timestamp in range(11, 111):
        observer.eventFilter(canvas, TabletEvent("TabletMove", 0.8, timestamp))
        observer.eventFilter(
            canvas, UnmarkedMouseEvent(event_type("MouseButtonPress"), 1.0, 4.0, timestamp)
        )
    observer.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 111))

    text = monitor.report.render({})
    source = qt_enum(QtCore.Qt, "MouseEventSource", "MouseEventNotSynthesized")
    assert "samples=101 pressure_min=0.20000 pressure_max=0.80000" in text
    assert f"mouse:PRESS:tablet-active:source={source}': 100" in text
    assert text.count("capture-ignore") == 4


def test_failed_brush_probe_does_not_drop_samples_or_call_capture_error(input_types) -> None:
    def unavailable_brush():
        raise RuntimeError("probe failed")

    monitor = Monitor()
    monitor.set_collection(True)
    canvas = object()
    samples, errors = [], []
    observer = ObservedInputFilter(
        FakeHost(canvas),
        lambda *args: samples.append(args),
        errors.append,
        monitor=monitor,
        brush_snapshot=unavailable_brush,
    )

    observer.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))
    observer.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 20))

    assert len(samples) == 2
    assert errors == []
    assert monitor.failure_count == 1
    assert "samples=1 pressure_min=0.20000" in monitor.report.render({})


def test_disabling_both_sinks_skips_capture_probes(input_types) -> None:
    monitor = Monitor()
    canvas = object()
    samples = []

    def unexpected_observation(*args):
        pytest.fail("disabled monitoring was invoked")

    monitor.observe = unexpected_observation
    observer = ObservedInputFilter(
        FakeHost(canvas), lambda *args: samples.append(args), lambda error: None, monitor=monitor
    )

    observer.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))
    observer.eventFilter(canvas, TabletEvent("TabletRelease", 0.0, 20))
    observer.reset()

    assert len(samples) == 2


def test_a_failing_collection_probe_cannot_prevent_core_error_delivery(input_types) -> None:
    monitor = Monitor(output=logging.StreamHandler(io.StringIO()))
    monitor.set_collection(True)
    canvas = object()
    errors = []

    def fail_sample(*args):
        raise ValueError("core failure")

    def fail_probe(*args):
        raise RuntimeError("probe failure")

    monitor.log = fail_probe
    observer = ObservedInputFilter(FakeHost(canvas), fail_sample, errors.append, monitor=monitor)

    observer.eventFilter(canvas, TabletEvent("TabletPress", 0.2, 10))

    assert len(errors) == 1
    assert str(errors[0]) == "core failure"
    assert monitor.failure_count > 0
