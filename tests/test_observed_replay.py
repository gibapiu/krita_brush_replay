from types import SimpleNamespace

import pytest

from krita_brush_replay.domain.recording import BrushSample
from krita_brush_replay.observability.monitor import Monitor
from krita_brush_replay.observability.replay import (
    ObservedNode,
    ObservedReplayer,
    ObservedSyntheticInput,
)


class Node:
    def __init__(self) -> None:
        self.calls = []

    def paintPolylineStroke(self, points, style="ForegroundColor"):
        self.calls.append((points, style))
        return "submitted"


class Replayer:
    def __init__(self) -> None:
        self.calls = []

    def replay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_disabled_replayer_passes_through_exact_objects_and_callbacks() -> None:
    core = Replayer()
    args = tuple(object() for _ in range(6))
    apply = object()
    observer = ObservedReplayer(core, Monitor())

    observer.replay(*args, apply_brush_state=apply)

    assert core.calls == [(args, {"apply_brush_state": apply})]


def test_native_probe_records_submitted_pressure_without_copying_points() -> None:
    monitor = Monitor()
    monitor.set_collection(True)
    node = Node()
    observed = ObservedNode(node, monitor)
    points = [
        SimpleNamespace(pressure=lambda: 0.2, time=lambda: 0),
        SimpleNamespace(pressure=lambda: 0.8, time=lambda: 30),
    ]

    result = observed.paintPolylineStroke(points, "BackgroundColor")

    assert result == "submitted"
    assert node.calls[0][0] is points
    assert node.calls[0][1] == "BackgroundColor"
    assert "replay-submit api=paintPolylineStroke index=1 samples=2" in monitor.report.render({})
    assert "pressure_min=0.20000 pressure_max=0.80000" in monitor.report.render({})


def test_probe_failure_does_not_prevent_native_submission() -> None:
    monitor = Monitor()
    monitor.set_collection(True)
    node = Node()
    points = [object()]

    assert ObservedNode(node, monitor).paintPolylineStroke(points) == "submitted"
    assert node.calls[0][0] is points
    assert monitor.failure_count == 1


def test_node_without_native_api_keeps_capability_detection_unchanged() -> None:
    node = ObservedNode(object(), Monitor())

    assert getattr(node, "paintPolylineStroke", None) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_synthetic_probe_preserves_samples_and_delivery_callbacks(enabled) -> None:
    class SyntheticInput:
        def prepare(self):
            return target

        def replay_stroke(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    monitor = Monitor()
    monitor.set_collection(enabled)
    core = SyntheticInput()
    target = object()
    samples = (BrushSample(1, 2, 0.2, 10), BrushSample(3, 4, 0.8, 20))
    callbacks = tuple(object() for _ in range(3))
    before_press = object()
    observer = ObservedSyntheticInput(core, monitor)

    assert observer.prepare() is target
    observer.replay_stroke(target, samples, *callbacks, before_press=before_press)

    assert core.args == (target, samples, *callbacks)
    assert core.args[1] is samples
    assert core.kwargs == {"before_press": before_press}
    assert ("api=synthetic-tablet-events" in monitor.report.render({})) is enabled


@pytest.mark.parametrize("fail", [False, True])
def test_monitoring_does_not_change_completion_or_brush_callback_delivery(fail) -> None:
    monitor = Monitor()
    monitor.set_collection(True)
    core = Replayer()
    session = SimpleNamespace(strokes=(SimpleNamespace(samples=(BrushSample(1, 2, 0.3, 0),)),))
    complete, errors, applied = [], [], []
    observer = ObservedReplayer(core, monitor, brush_snapshot=lambda: {})
    observer.replay(
        None,
        Node(),
        session,
        None,
        lambda: complete.append(True),
        errors.append,
        apply_brush_state=applied.append,
    )
    args, kwargs = core.calls[0]

    def broken_probe(*args):
        raise RuntimeError("monitor failed")

    monitor.log = broken_probe
    kwargs["apply_brush_state"]("brush")
    if fail:
        error = ValueError("paint failed")
        args[5](error)
        assert errors == [error]
        assert complete == []
    else:
        args[4]()
        assert complete == [True]
        assert errors == []
    assert applied == ["brush"]
