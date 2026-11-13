import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from krita_brush_replay.infrastructure.krita_pressure import KritaPressureAdapter


def application(curve_text="0,0;0.5,0.8;1,1;", *, enabled=True):
    return SimpleNamespace(
        action=Mock(return_value=SimpleNamespace(isChecked=lambda: enabled)),
        readSetting=Mock(return_value=curve_text),
    )


def test_mapping_uses_kritas_pressure_table_size_and_interpolation() -> None:
    app = application()
    curve_type = Mock()
    transfer = [0.0, 0.8, 1.0]
    curve_type.return_value.floatTransfer.return_value = transfer
    curve_type.interpolateLinear.return_value = 0.61
    adapter = KritaPressureAdapter(app, curve_type=curve_type)

    mapping = adapter.current_mapping()

    assert mapping(0.25) == 0.61
    app.action.assert_called_once_with("disable_pressure")
    app.readSetting.assert_called_once_with("", "tabletPressureCurve", "0,0;1,1;")
    curve_type.assert_called_once_with("0,0;0.5,0.8;1,1;")
    curve_type.return_value.floatTransfer.assert_called_once_with(1025)
    curve_type.interpolateLinear.assert_called_once_with(0.25, transfer)
    assert curve_type.interpolateLinear.call_args.args[1] is transfer


def test_new_mapping_reads_current_settings_without_mutating_an_ongoing_mapping() -> None:
    app = application()
    curve_type = Mock()
    first_curve, second_curve = Mock(), Mock()
    first_transfer, second_transfer = [0.0, 0.8, 1.0], [0.0, 0.3, 1.0]
    first_curve.floatTransfer.return_value = first_transfer
    second_curve.floatTransfer.return_value = second_transfer
    curve_type.side_effect = [first_curve, second_curve]
    curve_type.interpolateLinear.side_effect = lambda pressure, transfer: transfer[1]
    adapter = KritaPressureAdapter(app, curve_type=curve_type)

    original_mapping = adapter.current_mapping()
    app.readSetting.return_value = "0,0;0.5,0.3;1,1;"
    current_mapping = adapter.current_mapping()

    assert original_mapping(0.5) == 0.8
    assert current_mapping(0.5) == 0.3
    assert app.readSetting.call_count == 2
    assert [call.args[0] for call in curve_type.call_args_list] == [
        "0,0;0.5,0.8;1,1;",
        "0,0;0.5,0.3;1,1;",
    ]


def test_pressure_disabled_uses_full_pressure_without_loading_a_curve() -> None:
    app = application(enabled=False)
    curve_type = Mock()
    mapping = KritaPressureAdapter(app, curve_type=curve_type).current_mapping()

    assert [mapping(pressure) for pressure in (0.0, 0.25, 1.0)] == [1.0, 1.0, 1.0]
    app.readSetting.assert_not_called()
    curve_type.assert_not_called()


def test_missing_pressure_setting_reports_an_error() -> None:
    app = application()
    app.action.return_value = None

    with pytest.raises(RuntimeError, match="pen pressure setting is unavailable"):
        KritaPressureAdapter(app).current_mapping()


def test_default_curve_type_comes_from_the_loaded_krita_api(monkeypatch) -> None:
    api = ModuleType("krita")
    api.KisCubicCurve = Mock()
    api.KisCubicCurve.interpolateLinear.return_value = 0.75
    monkeypatch.setitem(sys.modules, "krita", api)

    mapping = KritaPressureAdapter(application()).current_mapping()

    assert mapping(0.4) == 0.75
    api.KisCubicCurve.return_value.floatTransfer.assert_called_once_with(1025)
