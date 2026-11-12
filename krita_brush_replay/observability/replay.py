from __future__ import annotations

from krita_brush_replay.observability.monitor import Monitor
from krita_brush_replay.observability.pressure import PressureTrace


class ObservedReplayer:
    def __init__(self, replayer, monitor: Monitor, *, brush_snapshot=None) -> None:
        self._replayer = replayer
        self._monitor = monitor
        self._brush_snapshot = brush_snapshot

    def replay(
        self, document, node, session, target, on_complete, on_error, *, apply_brush_state=None
    ) -> None:
        if not self._monitor.enabled:
            self._replayer.replay(
                document,
                node,
                session,
                target,
                on_complete,
                on_error,
                apply_brush_state=apply_brush_state,
            )
            return
        self._monitor.observe(self._record_request, session)

        def complete():
            self._monitor.observe(self._monitor.log, "replay-finished status=complete")
            on_complete()

        def fail(error):
            self._monitor.observe(self._monitor.log, "replay-finished error=%s", error)
            on_error(error)

        def apply(state):
            apply_brush_state(state)
            if self._brush_snapshot is not None:
                self._monitor.observe(self._record_brush)

        self._replayer.replay(
            document,
            ObservedNode(node, self._monitor),
            session,
            target,
            complete,
            fail,
            apply_brush_state=apply if apply_brush_state is not None else None,
        )

    def _record_brush(self) -> None:
        self._monitor.log("replay-brush %s", self._brush_snapshot())

    def _record_request(self, session) -> None:
        self._monitor.log("replay-request strokes=%s", len(session.strokes))


class ObservedNode:
    def __init__(self, node, monitor: Monitor) -> None:
        self._node = node
        self._monitor = monitor
        self._submissions = 0

    def __getattr__(self, name):
        value = getattr(self._node, name)
        if name != "paintPolylineStroke" or not callable(value):
            return value

        def paint(points, *args, **kwargs):
            self._monitor.observe(self._record_points, points)
            return value(points, *args, **kwargs)

        return paint

    def _record_points(self, points) -> None:
        trace = PressureTrace()
        for point in points:
            trace.observe_pressure(point.pressure(), int(point.time()))
        self._submissions += 1
        self._monitor.log(
            "replay-submit api=paintPolylineStroke index=%s %s", self._submissions, trace
        )


class ObservedSyntheticInput:
    def __init__(self, synthetic_input, monitor: Monitor) -> None:
        self._input = synthetic_input
        self._monitor = monitor

    def prepare(self):
        return self._input.prepare()

    def replay_stroke(self, target, samples, *args, **kwargs) -> None:
        if self._monitor.enabled:
            self._monitor.observe(self._record_samples, samples)
        self._input.replay_stroke(target, samples, *args, **kwargs)

    def _record_samples(self, samples) -> None:
        trace = PressureTrace()
        for sample in samples:
            trace.observe(sample)
        self._monitor.log("replay-submit api=synthetic-tablet-events %s", trace)
