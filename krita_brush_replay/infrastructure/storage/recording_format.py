
from __future__ import annotations

import json
import os
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass
from struct import Struct
from typing import BinaryIO

from krita_brush_replay.domain.brush_override import BrushStatePatch
from krita_brush_replay.domain.recording import (
    BrushSample,
    BrushState,
    ColorState,
    HistoryStep,
    PackedSamples,
    ResourceRef,
    Stroke,
)

Span = tuple[int, int]
SamplesAt = Callable[[Span, int], Sequence[BrushSample]]

_FRAME = Struct("<IB")
_STROKE_HEADER = Struct("<II")
_STROKE_KIND = 0
_KIND_BY_STEP = {HistoryStep.UNDO: 1, HistoryStep.REDO: 2}
_STEP_BY_KIND = {kind: step for step, kind in _KIND_BY_STEP.items()}
_SETTING_TYPES = {
    "preset": ResourceRef,
    "pattern": ResourceRef,
    "gradient": ResourceRef,
    "foreground": ColorState,
    "background": ColorState,
}


def encode_entry(entry: Stroke | HistoryStep) -> bytes:
    if isinstance(entry, HistoryStep):
        return _FRAME.pack(0, _KIND_BY_STEP[entry])
    state = entry.brush_state
    brush = json.dumps(
        _encoded_settings({field.name: getattr(state, field.name) for field in fields(state)})
    ).encode()
    payload = b"".join(
        (
            _STROKE_HEADER.pack(len(brush), len(entry.samples)),
            brush,
            zlib.compress(bytes(entry.samples), level=1),
        )
    )
    return _FRAME.pack(len(payload), _STROKE_KIND) + payload


def read_entries(source: BinaryIO, samples_at: SamplesAt) -> list[Stroke | HistoryStep]:
    size = source.seek(0, os.SEEK_END)
    source.seek(0)
    entries: list[Stroke | HistoryStep] = []
    brush_states: dict[bytes, BrushState] = {}
    while (start := source.tell()) + _FRAME.size <= size:
        length, kind = _FRAME.unpack(source.read(_FRAME.size))
        end = start + _FRAME.size + length
        if end > size:
            break
        if kind in _STEP_BY_KIND:
            entries.append(_STEP_BY_KIND[kind])
            continue
        if kind != _STROKE_KIND:
            raise ValueError(f"unknown recording entry {kind}")
        brush_length, sample_count = _STROKE_HEADER.unpack(source.read(_STROKE_HEADER.size))
        brush = source.read(brush_length)
        if brush not in brush_states:
            brush_states[brush] = BrushState(**_decoded_settings(json.loads(brush)))
        entries.append(Stroke(samples_at((start, end - start), sample_count), brush_states[brush]))
        source.seek(end)
    return entries


def decode_samples(frame: bytes) -> PackedSamples:
    brush_length, _sample_count = _STROKE_HEADER.unpack_from(frame, _FRAME.size)
    samples_start = _FRAME.size + _STROKE_HEADER.size + brush_length
    return PackedSamples.from_bytes(zlib.decompress(frame[samples_start:]))


def encode_overrides(overrides: Iterable[BrushStatePatch]) -> list[dict[str, object]]:
    return [
        _encoded_settings({name: getattr(patch, name) for name in patch.overridden_settings})
        for patch in overrides
    ]


def decode_overrides(data: Iterable[Mapping[str, object]]) -> tuple[BrushStatePatch, ...]:
    return tuple(BrushStatePatch(**_decoded_settings(patch)) for patch in data)


def _encoded_settings(settings: Mapping[str, object]) -> dict[str, object]:
    return {
        name: asdict(value) if is_dataclass(value) else value for name, value in settings.items()
    }


def _decoded_settings(data: Mapping[str, object]) -> dict[str, object]:
    return {name: _decoded_setting(name, value) for name, value in data.items()}


def _decoded_setting(name: str, value: object) -> object:
    setting_type = _SETTING_TYPES.get(name)
    if setting_type is ColorState:
        return ColorState(**{**value, "components": tuple(value["components"])})
    if setting_type is not None:
        return setting_type(**value)
    return value
