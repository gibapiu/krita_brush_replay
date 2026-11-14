from __future__ import annotations

from collections.abc import Callable

PRESSURE_TRANSFER_SIZE = 1025


class KritaPressureAdapter:
    def __init__(self, application, *, curve_type=None) -> None:
        self._application = application
        self._curve_type = curve_type

    def current_mapping(self) -> Callable[[float], float]:
        pressure_action = self._application.action("disable_pressure")
        if pressure_action is None:
            raise RuntimeError("Krita's pen pressure setting is unavailable")
        if not pressure_action.isChecked():
            return lambda pressure: 1.0

        curve_type = self._curve_type
        if curve_type is None:
            from krita import KisCubicCurve

            curve_type = KisCubicCurve
        curve_text = self._application.readSetting("", "tabletPressureCurve", "0,0;1,1;")
        transfer = curve_type(curve_text).floatTransfer(PRESSURE_TRANSFER_SIZE)

        def map_pressure(pressure: float) -> float:
            return curve_type.interpolateLinear(pressure, transfer)

        return map_pressure
