"""Behavioral checks for complete gestures and cancellation cleanup."""

from collections import deque

import pytest

from krita_brush_replay.domain.recording import BrushSample, InputPhase
from krita_brush_replay.infrastructure.synthetic_input import (
    CanvasInputTarget,
    SyntheticInputReplayer,
)


class EventLoop:
    def __init__(self) -> None:
        self.pending = deque()
        self.events = []
        self.posted_at = []
        self.now_ms = 0
        self.delivery_ms = 5

    def call_soon(self, callback) -> None:
        self.call_later(0, callback)

    def call_later(self, delay_ms, callback) -> None:
        self.pending.append((delay_ms, callback))

    def post(self, receiver, event, on_delivered, on_error) -> None:
        self.events.append(event)
        self.posted_at.append(self.now_ms)
        self.call_later(self.delivery_ms, on_delivered)

    def step(self) -> None:
        delay_ms, callback = self.pending.popleft()
        self.now_ms += delay_ms
        callback()

    def run(self) -> None:
        while self.pending:
            self.step()


class Events:
    def enter(self, receiver, position):
        return "enter"

    def leave(self):
        return "leave"

    def hover(self, receiver, position, sample, timestamp_ms):
        return "hover"

    def create(self, phase, receiver, position, sample, timestamp_ms):
        return phase, position, sample, timestamp_ms


@pytest.fixture
def replay(monkeypatch):
    loop = EventLoop()
    focused = []
    valid = [True]
    target = CanvasInputTarget(
        object(),
        lambda point: (point[0] + 10, point[1] + 20),
        lambda: valid[0],
        lambda: focused.append(True),
    )
    monkeypatch.setattr(
        "krita_brush_replay.infrastructure.synthetic_input._require_gui_thread", lambda: None
    )
    monkeypatch.setattr(
        "krita_brush_replay.infrastructure.synthetic_input.monotonic", lambda: loop.now_ms / 1000
    )
    replayer = SyntheticInputReplayer(
        lambda: target, loop, event_factory=Events(), event_poster=loop
    )
    return replayer, loop, focused, valid


@pytest.mark.parametrize("tap", [False, True])
def test_replays_complete_gesture_before_reporting_completion(replay, tap) -> None:
    replayer, loop, focused, _valid = replay
    samples = (
        BrushSample(1.25, 2.5, 0.25, 100, x_tilt=-8, rotation=35),
        BrushSample(3.75, 4.5, 0.75, 125, y_tilt=14, tangential_pressure=0.6),
    )
    if tap:
        samples = samples[:1]
    completed = []
    errors = []
    prepared = []
    replayer.replay_stroke(
        replayer.prepare(),
        samples,
        lambda: None,
        lambda: completed.append(list(loop.events)),
        errors.append,
        before_press=lambda: prepared.append(list(loop.events)),
    )
    assert focused == []
    loop.step()

    assert focused == [True]
    assert loop.events == ["enter"]
    assert completed == []

    loop.run()

    assert prepared == [["enter", "hover"]]
    assert loop.events[:2] == ["enter", "hover"]
    gesture = loop.events[2:-1]
    assert [event[0] for event in gesture] == (
        [InputPhase.PRESS, InputPhase.RELEASE]
        if tap
        else [InputPhase.PRESS, InputPhase.MOVE, InputPhase.RELEASE]
    )
    assert gesture[0][1] == (11.25, 22.5)
    assert gesture[-1][2] == samples[-1]
    assert loop.events[-1] == "leave"
    assert completed == [loop.events]
    assert errors == []
    if not tap:
        assert gesture[1][1] == (13.75, 24.5)
        assert gesture[1][3] - gesture[0][3] == 25
        # Delivery latency must not accumulate on top of the recorded duration.
        assert loop.posted_at[3] - loop.posted_at[2] == 25


@pytest.mark.parametrize("change", ["context", "canvas", "delivery"])
def test_failure_releases_the_original_canvas_and_leaves_before_reporting(replay, change) -> None:
    replayer, loop, _focused, valid = replay
    errors = []
    completed = []
    context_valid = [True]

    def require_context() -> None:
        if not context_valid[0]:
            raise RuntimeError("the active document or layer changed")

    replayer.replay_stroke(
        replayer.prepare(),
        (BrushSample(1, 2, 0.5, 0), BrushSample(3, 4, 0.8, 100)),
        require_context,
        lambda: completed.append(True),
        lambda error: errors.append((error, list(loop.events))),
    )
    while not any(
        isinstance(event, tuple) and event[0] is InputPhase.PRESS for event in loop.events
    ):
        loop.step()
    loop.step()  # Deliver press before changing the context.
    if change == "context":
        context_valid[0] = False
    elif change == "canvas":
        valid[0] = False
    else:
        original_post = loop.post

        def fail_move(receiver, event, on_delivered, on_error) -> None:
            if isinstance(event, tuple) and event[0] is InputPhase.MOVE:
                raise RuntimeError("could not queue move")
            original_post(receiver, event, on_delivered, on_error)

        loop.post = fail_move
    loop.run()

    assert [event[0] for event in loop.events if isinstance(event, tuple)] == [
        InputPhase.PRESS,
        InputPhase.RELEASE,
    ]
    assert loop.events[-1] == "leave"
    assert len(errors) == 1
    assert errors[0][1] == loop.events
    assert completed == []
