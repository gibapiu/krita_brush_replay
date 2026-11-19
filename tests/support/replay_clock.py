"""Virtual replay deadlines and controlled external delivery latency."""

import heapq
from itertools import count

from krita_brush_replay.infrastructure import synthetic_input
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtWidgets, qt_enum


class VirtualClock:
    """External scheduler with absolute deadlines and stable ordering for simultaneous work."""

    def __init__(self):
        self.now_ms = 0
        self.pending = []
        self.order = count()

    def call_soon(self, callback):
        self.call_later(0, callback)

    def call_later(self, delay_ms, callback):
        heapq.heappush(self.pending, (self.now_ms + delay_ms, next(self.order), callback))

    def drain(self):
        for _ in range(10_000):
            if not self.pending:
                return
            self.now_ms, _, callback = heapq.heappop(self.pending)
            callback()
            QtWidgets.QApplication.processEvents()
        raise AssertionError("Replay exceeded the bounded virtual scheduler workload")


class DelayedDelivery:
    """Use real Qt posting while adding known host delivery latency to acknowledgements."""

    def __init__(self, clock):
        self.clock = clock
        self.poster = synthetic_input.QtEventPoster()
        self.stroke_move_times = []

    def post(self, receiver, event, on_delivered, on_error):
        if event.type() == qt_enum(QtCore.QEvent, "Type", "TabletMove") and (
            event.buttons() == qt_enum(QtCore.Qt, "MouseButton", "LeftButton")
        ):
            self.stroke_move_times.append(self.clock.now_ms)
        self.poster.post(receiver, event, lambda: self.clock.call_later(5, on_delivered), on_error)
