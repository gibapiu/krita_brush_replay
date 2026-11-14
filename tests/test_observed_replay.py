import logging
from types import SimpleNamespace

import pytest

from krita_brush_replay.observability.replay import ObservedReplayer


class Replayer:
    def replay(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


@pytest.mark.parametrize("level", [logging.WARNING, logging.DEBUG])
@pytest.mark.parametrize("fail", [False, True])
def test_logging_preserves_replay_objects_and_callbacks(caplog, level, fail) -> None:
    caplog.set_level(level, logger="krita_brush_replay")
    core = Replayer()
    document, node, target = object(), object(), object()
    session = SimpleNamespace(strokes=(object(),))
    complete, errors, applied = [], [], []
    apply = applied.append

    ObservedReplayer(core).replay(
        document, node, session, target, lambda: complete.append(True), errors.append,
        apply_brush_state=apply,
    )

    assert all(
        actual is expected
        for actual, expected in zip(core.args[:4], (document, node, session, target))
    )
    assert core.kwargs["apply_brush_state"] is apply
    core.kwargs["apply_brush_state"]("brush")
    if fail:
        error = ValueError("paint failed")
        core.args[5](error)
        assert errors == [error]
        assert complete == []
        assert caplog.records[-1].levelno == logging.ERROR
        assert caplog.records[-1].exc_info[1] is error
    else:
        core.args[4]()
        assert complete == [True]
        assert errors == []
    assert applied == ["brush"]
    expected = ["replay-request strokes=1"] if level == logging.DEBUG else []
    if fail:
        expected.append("replay-failed")
    elif level == logging.DEBUG:
        expected.append("replay-finished status=complete")
    assert caplog.messages == expected


@pytest.mark.parametrize("fail", [False, True])
def test_broken_logging_cannot_prevent_callback_delivery(monkeypatch, fail) -> None:
    from krita_brush_replay.observability.replay import logger

    def broken_log(*args, **kwargs):
        raise OSError("output unavailable")

    monkeypatch.setattr(logger, "log", broken_log)
    core = Replayer()
    complete, errors = [], []
    ObservedReplayer(core).replay(
        object(), object(), SimpleNamespace(strokes=()), object(),
        lambda: complete.append(True), errors.append,
    )

    assert core.kwargs["apply_brush_state"] is None
    if fail:
        error = ValueError("paint failed")
        core.args[5](error)
        assert errors == [error]
        assert complete == []
    else:
        core.args[4]()
        assert complete == [True]
        assert errors == []


def test_core_callback_exceptions_are_not_swallowed() -> None:
    core = Replayer()

    def broken_callback():
        raise ValueError("callback failed")

    ObservedReplayer(core).replay(
        None, None, SimpleNamespace(strokes=()), None, broken_callback, None,
    )

    with pytest.raises(ValueError, match="callback failed"):
        core.args[4]()
