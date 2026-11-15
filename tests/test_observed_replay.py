import logging
from types import SimpleNamespace

import pytest

from krita_brush_replay.observability.replay import ObservedReplayer

SESSION = SimpleNamespace(strokes=(object(),))


class Replayer:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def replay(self, document, node, session, target, on_complete, on_error, **kwargs):
        if self._error is None:
            on_complete()
        else:
            on_error(self._error)


def test_successful_replay_is_logged_and_completes(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="krita_brush_replay")
    completed, errors = [], []

    ObservedReplayer(Replayer()).replay(
        None, None, SESSION, None, lambda: completed.append(True), errors.append
    )

    assert (completed, errors) == ([True], [])
    assert caplog.messages == ["replay-request strokes=1", "replay-finished status=complete"]


def test_failed_replay_is_logged_and_reported(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="krita_brush_replay")
    error = ValueError("paint failed")
    completed, errors = [], []

    ObservedReplayer(Replayer(error)).replay(
        None, None, SESSION, None, lambda: completed.append(True), errors.append
    )

    assert (completed, errors) == ([], [error])
    assert caplog.messages == ["replay-failed"]
    assert caplog.records[0].exc_info[1] is error


def test_synchronous_replay_failure_is_logged_and_propagated(caplog) -> None:
    class FailingReplayer:
        def replay(self, *args, **kwargs):
            raise RuntimeError("no canvas")

    caplog.set_level(logging.ERROR, logger="krita_brush_replay")

    with pytest.raises(RuntimeError, match="no canvas"):
        ObservedReplayer(FailingReplayer()).replay(None, None, SESSION, None, None, None)

    assert caplog.messages == ["replay-failed"]
