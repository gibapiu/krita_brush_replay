from __future__ import annotations

from collections.abc import Callable

PRESSURE_TRANSFER_SIZE = 1025


class KritaPressureAdapter:
    def __init__(self, application) -> None:
        self._application = application

    def current_mapping(self) -> Callable[[float], float]:
        pressure_action = self._application.action("disable_pressure")
        if pressure_action is None:
            raise RuntimeError("Krita's pen pressure setting is unavailable")
        if not pressure_action.isChecked():
            return lambda pressure: 1.0

        from krita import KisCubicCurve

        curve_text = self._application.readSetting("", "tabletPressureCurve", "0,0;1,1;")
        transfer = KisCubicCurve(curve_text).floatTransfer(PRESSURE_TRANSFER_SIZE)
        return lambda pressure: KisCubicCurve.interpolateLinear(pressure, transfer)
