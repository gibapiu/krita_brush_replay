from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace

from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef

MEANINGFUL_DECIMALS = {"size": 2, "rotation": 2, "opacity": 4, "flow": 4}


@dataclass(frozen=True, slots=True)
class BrushStatePatch:
    preset: ResourceRef | None = None
    foreground: ColorState | None = None
    background: ColorState | None = None
    size: float | None = None
    opacity: float | None = None
    flow: float | None = None
    rotation: float | None = None
    pattern: ResourceRef | None = None
    gradient: ResourceRef | None = None
    blending_mode: str | None = None

    @property
    def overridden_settings(self) -> frozenset[str]:
        return frozenset(
            setting.name for setting in fields(self) if getattr(self, setting.name) is not None
        )

    def with_setting(self, name: str, value: object) -> BrushStatePatch:
        return replace(self, **{name: value})

    def without_setting(self, name: str) -> BrushStatePatch:
        return replace(self, **{name: None})

    def apply_to(self, state: BrushState) -> BrushState:
        return replace(
            state,
            **{name: getattr(self, name) for name in self.overridden_settings},
        )


@dataclass(frozen=True, slots=True)
class StrokeSettings:
    recorded: BrushState
    override: BrushStatePatch = BrushStatePatch()
    effective: BrushState = field(init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective", self.override.apply_to(self.recorded))

    def overriding(self, setting: str, value: object) -> StrokeSettings:
        if _same_value(setting, getattr(self.recorded, setting), value):
            return self.resetting(setting)
        return replace(self, override=self.override.with_setting(setting, value))

    def resetting(self, setting: str) -> StrokeSettings:
        return replace(self, override=self.override.without_setting(setting))


@dataclass(frozen=True, slots=True)
class BrushChoices:
    presets: tuple[ResourceRef, ...] = ()
    patterns: tuple[ResourceRef, ...] = ()
    gradients: tuple[ResourceRef, ...] = ()
    blending_modes: Mapping[str, str] = field(default_factory=dict)


def _same_value(setting: str, recorded: object, value: object) -> bool:
    decimals = MEANINGFUL_DECIMALS.get(setting)
    if decimals is None:
        return recorded == value
    return round(recorded, decimals) == round(value, decimals)
