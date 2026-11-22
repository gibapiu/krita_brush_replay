"""Encoding for the temporary capture log."""

import json
from dataclasses import asdict
from struct import Struct

from krita_brush_replay.domain.recording import BrushState, ColorState, ResourceRef

MAGIC = b"KBR\x02"
HEADER = Struct("<BI")
STROKE = Struct("<QQQ")
BRUSH, SAMPLES, FINISH = range(1, 4)


def encode_stroke(brush_reference: int, sample_offset: int, sample_count: int) -> bytes:
    return STROKE.pack(brush_reference, sample_offset, sample_count)


def encode_brush(brush: BrushState) -> bytes:
    return json.dumps(asdict(brush)).encode("utf-8")


def decode_brush(data: bytes) -> BrushState:
    values = json.loads(data)
    for name in ("preset", "pattern", "gradient"):
        values[name] = ResourceRef(**values[name])
    for name in ("foreground", "background"):
        values[name]["components"] = tuple(values[name]["components"])
        values[name] = ColorState(**values[name])
    return BrushState(**values)


def read_record(stream, offset: int) -> tuple[int, bytes, int]:
    if stream.closed:
        raise OSError("recording file is closed")
    stream.seek(offset)
    header = stream.read(HEADER.size)
    if len(header) != HEADER.size:
        raise OSError("recording data is incomplete")
    kind, length = HEADER.unpack(header)
    data = stream.read(length)
    if len(data) != length:
        raise OSError("recording data is incomplete")
    return kind, data, offset + HEADER.size + length
