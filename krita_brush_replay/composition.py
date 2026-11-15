"""Assemble the runtime with logging decorators outside the core layers."""

from __future__ import annotations

from krita_brush_replay.infrastructure.krita_host import KritaHost
from krita_brush_replay.infrastructure.krita_replay import KritaReplayer
from krita_brush_replay.observability.capture import ObservedInputFilter
from krita_brush_replay.observability.replay import ObservedReplayer
from krita_brush_replay.runtime import BrushReplayRuntime


def _create_replayer(*args, **kwargs) -> ObservedReplayer:
    return ObservedReplayer(KritaReplayer(*args, **kwargs))


def compose_runtime(panel, application) -> BrushReplayRuntime:
    return BrushReplayRuntime(
        panel,
        krita_instance=application,
        host_factory=lambda application: KritaHost(application, replayer_factory=_create_replayer),
        input_filter_factory=ObservedInputFilter,
    )
