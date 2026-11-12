from krita_brush_replay.infrastructure.input_events import event_type, left_button
from krita_brush_replay.infrastructure.qt_facade import QtCore, qt_enum


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self._x = x
        self._y = y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class FakeEvent:
    def __init__(
        self,
        event_type,
        x: float,
        y: float,
        timestamp: int,
    ) -> None:
        self._event_type = event_type
        self._point = FakePoint(x, y)
        self._timestamp = timestamp

    def type(self):
        return self._event_type

    def button(self):
        return left_button()

    def position(self) -> FakePoint:
        return self._point

    def timestamp(self) -> int:
        return self._timestamp


class FakeHost:
    def __init__(self, canvas_target) -> None:
        self.canvas_target = canvas_target
        self.mapper_calls = 0
        self.mapped_positions: list[tuple[float, float]] = []

    def is_canvas_target(self, target) -> bool:
        return target is self.canvas_target

    def capture_mapper(self):
        self.mapper_calls += 1

        def to_image(position: tuple[float, float]) -> tuple[float, float]:
            self.mapped_positions.append(position)
            return position[0] + 10.0, position[1] + 20.0

        return to_image


def mouse_event(name: str, x: float, timestamp: int) -> FakeEvent:
    return FakeEvent(event_type(name), x, 4.0, timestamp)


class TabletEvent(FakeEvent):
    def __init__(self, name: str, pressure: float, timestamp: int) -> None:
        super().__init__(event_type(name), float(timestamp), 4.0, timestamp)
        self._pressure = pressure

    def pressure(self) -> float:
        return self._pressure


class UnmarkedMouseEvent(FakeEvent):
    def source(self):
        return qt_enum(QtCore.Qt, "MouseEventSource", "MouseEventNotSynthesized")
