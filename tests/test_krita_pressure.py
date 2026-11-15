from types import SimpleNamespace

import pytest

from krita_brush_replay.infrastructure.krita_pressure import KritaPressureAdapter


def test_missing_pressure_setting_reports_an_error() -> None:
    application = SimpleNamespace(action=lambda name: None)

    with pytest.raises(RuntimeError, match="pen pressure setting is unavailable"):
        KritaPressureAdapter(application).current_mapping()
