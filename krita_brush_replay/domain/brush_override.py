from __future__ import annotations

from collections.abc import Iterable, Mapping
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


SETTINGS = tuple(setting.name for setting in fields(BrushStatePatch))


@dataclass(frozen=True, slots=True)
class BrushChoices:
    presets: tuple[ResourceRef, ...] = ()
    patterns: tuple[ResourceRef, ...] = ()
    gradients: tuple[ResourceRef, ...] = ()
    blending_modes: Mapping[str, str] = field(default_factory=dict)


def _shown(setting: str, value: object) -> object:
    decimals = MEANINGFUL_DECIMALS.get(setting)
    return value if decimals is None else round(value, decimals)


@dataclass(frozen=True, slots=True)
class RecordingSettings:
    stroke_count: int = 0
    recorded: Mapping[str, object] = field(default_factory=dict)
    override: BrushStatePatch = BrushStatePatch()

    @classmethod
    def summarize(
        cls,
        stroke_count: int,
        brushes: Iterable[BrushState],
        override: BrushStatePatch,
    ) -> RecordingSettings:
        common: dict[str, object] = {}
        for brush in brushes:
            for name in SETTINGS:
                value = _shown(name, getattr(brush, name))
                if name not in common:
                    common[name] = value
                elif common[name] != value:
                    common[name] = None
        return cls(stroke_count, common, override)

    @property
    def effective(self) -> dict[str, object]:
        overridden = self.override.overridden_settings
        return {
            name: _shown(name, getattr(self.override, name))
            if name in overridden
            else self.recorded.get(name)
            for name in SETTINGS
        }

    def overriding(self, setting: str, value: object) -> BrushStatePatch:
        recorded = self.recorded.get(setting)
        if recorded is not None and _shown(setting, value) == recorded:
            return self.override.without_setting(setting)
        return self.override.with_setting(setting, value)
