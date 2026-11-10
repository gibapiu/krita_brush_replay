from __future__ import annotations

from collections.abc import Callable, Sequence
from math import log10

from krita_brush_replay.domain.brush_override import (
    MEANINGFUL_DECIMALS,
    BrushChoices,
    StrokeSettings,
)
from krita_brush_replay.domain.recording import ColorState, ResourceRef
from krita_brush_replay.infrastructure.qt_facade import QtCore, QtGui, QtWidgets, qt_enum

RESET_SYMBOL = "↺"
MIXED_VALUES_TEXT = "—"
SWATCH_SIZE = 14
STEPPING_KEYS = ("Key_Up", "Key_Down", "Key_PageUp", "Key_PageDown", "Key_Home", "Key_End")


class WheelRequiresFocus:
    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NumberField(WheelRequiresFocus, QtWidgets.QDoubleSpinBox):
    edited = QtCore.pyqtSignal(object)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        *,
        suffix: str,
        decimals: int,
        scale: float = 1.0,
    ) -> None:
        super().__init__()
        display_decimals = decimals - round(log10(scale))
        self._scale = scale
        self._minimum = minimum
        self._shown_value: float | None = None
        self._mixed_value = minimum - 10**-display_decimals
        self.setDecimals(display_decimals)
        self.setRange(self._mixed_value, maximum)
        self.setSpecialValueText(MIXED_VALUES_TEXT)
        self.setSuffix(suffix)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(qt_enum(QtCore.Qt, "FocusPolicy", "StrongFocus"))
        self.valueChanged.connect(self._emit_edit)

    def show_value(self, value: float | None) -> None:
        self._shown_value = value
        was_blocked = self.blockSignals(True)
        try:
            self.setValue(self._mixed_value if value is None else value * self._scale)
        finally:
            self.blockSignals(was_blocked)

    def keyPressEvent(self, event) -> None:
        if self.lineEdit().text() == MIXED_VALUES_TEXT and event.text().strip():
            self.lineEdit().selectAll()
        super().keyPressEvent(event)

    def stepBy(self, steps: int) -> None:
        if self._is_mixed():
            return
        self.setValue(max(self._minimum, self.value() + steps * self.singleStep()))

    def _is_mixed(self) -> bool:
        return self.value() < self._minimum

    def _emit_edit(self, value: float) -> None:
        if self._is_mixed():
            self.show_value(self._shown_value)
        else:
            self.edited.emit(value / self._scale)


class ChoiceField(WheelRequiresFocus, QtWidgets.QComboBox):
    edited = QtCore.pyqtSignal(object)

    def __init__(self, label: Callable[[object], str]) -> None:
        super().__init__()
        self._label = label
        self._values: list[object] = []
        self.setSizeAdjustPolicy(
            qt_enum(
                QtWidgets.QComboBox,
                "SizeAdjustPolicy",
                "AdjustToMinimumContentsLengthWithIcon",
            )
        )
        self.setMinimumContentsLength(8)
        self.setPlaceholderText(MIXED_VALUES_TEXT)
        self.setFocusPolicy(qt_enum(QtCore.Qt, "FocusPolicy", "StrongFocus"))
        self.activated.connect(lambda index: self.edited.emit(self._values[index]))
        self._stepping_keys = {qt_enum(QtCore.Qt, "Key", key) for key in STEPPING_KEYS}

    def wheelEvent(self, event) -> None:
        if self._is_mixed():
            event.ignore()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._is_mixed() and event.key() in self._stepping_keys:
            event.ignore()
        else:
            super().keyPressEvent(event)

    def set_choices(self, values: Sequence[object]) -> None:
        self.clear()
        self._values = []
        for value in values:
            self._add(value)

    def show_value(self, value: object | None) -> None:
        if value is None:
            self.setCurrentIndex(-1)
            return
        if value not in self._values:
            self._add(value)
        self.setCurrentIndex(self._values.index(value))

    def _is_mixed(self) -> bool:
        return self.currentIndex() < 0

    def _add(self, value: object) -> None:
        self._values.append(value)
        self.addItem(self._label(value))


class ColorField(QtWidgets.QPushButton):
    edited = QtCore.pyqtSignal(object)

    def __init__(self, color_codec, title: str) -> None:
        super().__init__()
        self._color_codec = color_codec
        self._title = title
        self._color: ColorState | None = None
        self.clicked.connect(self._pick)

    def show_value(self, color: ColorState | None) -> None:
        self._color = color
        if color is None:
            self.setIcon(QtGui.QIcon())
            self.setText(MIXED_VALUES_TEXT)
            return
        qcolor = self._color_codec.to_qcolor(color)
        swatch = QtGui.QPixmap(SWATCH_SIZE, SWATCH_SIZE)
        swatch.fill(qcolor)
        self.setIcon(QtGui.QIcon(swatch))
        self.setText(qcolor.name())

    def _pick(self) -> None:
        initial = QtGui.QColor()
        if self._color is not None:
            initial = self._color_codec.to_qcolor(self._color)
        dialog = QtWidgets.QColorDialog(initial, self)
        dialog.setWindowTitle(self._title)
        picked_colors = []
        dialog.currentColorChanged.connect(picked_colors.append)
        if dialog.exec() and picked_colors:
            self.edited.emit(self._color_codec.from_qcolor(dialog.selectedColor()))


class StrokeSettingsEditor(QtWidgets.QGroupBox):
    override_requested = QtCore.pyqtSignal(str, object)
    reset_requested = QtCore.pyqtSignal(str)

    def __init__(self, color_codec, parent=None) -> None:
        super().__init__(parent)
        self._blending_mode_labels: dict[str, str] = {}
        self._fields = {
            "preset": ("Preset", ChoiceField(_resource_name)),
            "size": ("Size", _number_field("size", 0.01, 10_000.0, " px")),
            "opacity": ("Opacity", _number_field("opacity", 0.0, 100.0, " %", scale=100.0)),
            "flow": ("Flow", _number_field("flow", 0.0, 100.0, " %", scale=100.0)),
            "rotation": ("Rotation", _number_field("rotation", 0.0, 359.99, "°")),
            "foreground": ("Foreground", ColorField(color_codec, "Foreground colour")),
            "background": ("Background", ColorField(color_codec, "Background colour")),
            "pattern": ("Pattern", ChoiceField(_resource_name)),
            "gradient": ("Gradient", ChoiceField(_resource_name)),
            "blending_mode": ("Blending mode", ChoiceField(self._blending_mode_label)),
        }
        self._reset_buttons: dict[str, QtWidgets.QToolButton] = {}

        layout = QtWidgets.QFormLayout(self)
        for setting, (label, field) in self._fields.items():
            reset_button = QtWidgets.QToolButton()
            reset_button.setText(RESET_SYMBOL)
            reset_button.setToolTip(f"Reset {label.lower()} to the recorded value")
            reset_button.clicked.connect(
                lambda _checked=False, name=setting: self.reset_requested.emit(name)
            )
            field.edited.connect(
                lambda value, name=setting: self.override_requested.emit(name, value)
            )
            row = QtWidgets.QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(reset_button)
            layout.addRow(label, row)
            self._reset_buttons[setting] = reset_button
        self.show_settings(())

    def set_choices(self, choices: BrushChoices) -> None:
        self._choice_field("preset").set_choices(choices.presets)
        self._choice_field("pattern").set_choices(choices.patterns)
        self._choice_field("gradient").set_choices(choices.gradients)
        self._blending_mode_labels = dict(choices.blending_modes)
        self._choice_field("blending_mode").set_choices(tuple(choices.blending_modes))

    def show_settings(self, selected: Sequence[StrokeSettings]) -> None:
        self.setTitle(f"Editing strokes: {len(selected)}" if selected else "Select strokes to edit")
        effective_states = [stroke.effective for stroke in selected]
        overridden = set().union(*(stroke.override.overridden_settings for stroke in selected))
        for setting, (_label, field) in self._fields.items():
            values = {
                _editable_value(setting, getattr(state, setting)) for state in effective_states
            }
            field.show_value(values.pop() if len(values) == 1 else None)
            self._reset_buttons[setting].setEnabled(setting in overridden)

    def _choice_field(self, setting: str) -> ChoiceField:
        return self._fields[setting][1]

    def _blending_mode_label(self, mode: str) -> str:
        return self._blending_mode_labels.get(mode, mode)


def _number_field(
    setting: str,
    minimum: float,
    maximum: float,
    suffix: str,
    *,
    scale: float = 1.0,
) -> NumberField:
    decimals = MEANINGFUL_DECIMALS[setting]
    return NumberField(minimum, maximum, suffix=suffix, decimals=decimals, scale=scale)


def _editable_value(setting: str, value: object) -> object:
    decimals = MEANINGFUL_DECIMALS.get(setting)
    return value if decimals is None else round(value, decimals)


def _resource_name(resource: ResourceRef) -> str:
    return resource.name
