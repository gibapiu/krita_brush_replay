from __future__ import annotations

import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import starmap
from struct import Struct
from typing import overload

_SAMPLE = Struct("<dddqddddd")
_BLOCK_HEADER = Struct("<HBH")
_FORMAT = b"KBS\x01"
_BLOCK_SAMPLES = 256
_BLOCK_BYTES = _BLOCK_SAMPLES * _SAMPLE.size
_RAW = 0
_SHUFFLED_ZLIB = 1


@dataclass(frozen=True, slots=True)
class BrushSample:
    x: float
    y: float
    pressure: float
    timestamp_ms: int
    x_tilt: float = 0.0
    y_tilt: float = 0.0
    rotation: float = 0.0
    tangential_pressure: float = 0.0
    speed: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.pressure <= 1.0:
            raise ValueError("pressure must be between 0.0 and 1.0")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must not be negative")
        if not 0.0 <= self.speed <= 1.0:
            raise ValueError("speed must be between 0.0 and 1.0")


def _encode_block(data: bytes) -> bytes:
    shuffled = b"".join(data[offset :: _SAMPLE.size] for offset in range(_SAMPLE.size))
    compressed = zlib.compress(shuffled, level=1)
    encoding, payload = (
        (_SHUFFLED_ZLIB, compressed) if len(compressed) < len(data) else (_RAW, data)
    )
    return _BLOCK_HEADER.pack(len(data) // _SAMPLE.size, encoding, len(payload)) + payload


def _decode_block(block: bytes) -> bytes:
    count, encoding, length = _BLOCK_HEADER.unpack_from(block)
    if not 0 < count <= _BLOCK_SAMPLES or len(block) != _BLOCK_HEADER.size + length:
        raise ValueError("invalid sample block length")
    payload = block[_BLOCK_HEADER.size :]
    expected = count * _SAMPLE.size
    if encoding == _RAW:
        if len(payload) != expected:
            raise ValueError("incomplete raw sample block")
        return payload
    if encoding != _SHUFFLED_ZLIB:
        raise ValueError("unknown sample block encoding")
    decoder = zlib.decompressobj()
    try:
        shuffled = decoder.decompress(payload, expected + 1)
    except zlib.error as error:
        raise ValueError("corrupt compressed sample block") from error
    if len(shuffled) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError("invalid compressed sample block length")
    data = bytearray(expected)
    for offset in range(_SAMPLE.size):
        data[offset :: _SAMPLE.size] = shuffled[offset * count : (offset + 1) * count]
    return bytes(data)


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class CompactBrushSamples(Sequence[BrushSample]):
    _blocks: tuple[bytes, ...]
    _count: int

    def __init__(self, samples: Iterable[BrushSample]) -> None:
        builder = CompactBrushSamplesBuilder()
        for sample in samples:
            builder.append(sample)
        compacted = builder.finish()
        object.__setattr__(self, "_blocks", compacted._blocks)
        object.__setattr__(self, "_count", compacted._count)

    @classmethod
    def _from_blocks(cls, blocks: tuple[bytes, ...], count: int) -> CompactBrushSamples:
        samples = object.__new__(cls)
        object.__setattr__(samples, "_blocks", blocks)
        object.__setattr__(samples, "_count", count)
        return samples

    @classmethod
    def from_bytes(cls, data: bytes) -> CompactBrushSamples:
        if not data.startswith(_FORMAT):
            raise ValueError("unsupported sample format")
        blocks: list[bytes] = []
        offset, total, count = len(_FORMAT), 0, _BLOCK_SAMPLES
        while offset < len(data):
            if count != _BLOCK_SAMPLES or len(data) - offset < _BLOCK_HEADER.size:
                raise ValueError("incomplete sample block")
            count, _encoding, length = _BLOCK_HEADER.unpack_from(data, offset)
            if length > _BLOCK_BYTES:
                raise ValueError("sample block exceeds its size limit")
            end = offset + _BLOCK_HEADER.size + length
            block = bytes(data[offset:end])
            for values in _SAMPLE.iter_unpack(_decode_block(block)):
                BrushSample(*values)
            blocks.append(block)
            total += count
            offset = end
        return cls._from_blocks(tuple(blocks), total)

    def __bytes__(self) -> bytes:
        return b"".join((_FORMAT, *self._blocks))

    def __len__(self) -> int:
        return self._count

    @overload
    def __getitem__(self, index: int) -> BrushSample: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[BrushSample, ...]: ...

    def __getitem__(self, index: int | slice) -> BrushSample | tuple[BrushSample, ...]:
        if isinstance(index, slice):
            return tuple(self._selected(range(len(self))[index]))
        position = range(len(self))[index]
        block_index, sample_index = divmod(position, _BLOCK_SAMPLES)
        data = _decode_block(self._blocks[block_index])
        return BrushSample(*_SAMPLE.unpack_from(data, sample_index * _SAMPLE.size))

    def __iter__(self) -> Iterator[BrushSample]:
        for block in self._blocks:
            yield from starmap(BrushSample, _SAMPLE.iter_unpack(_decode_block(block)))

    def _selected(self, positions: range) -> Iterator[BrushSample]:
        previous_block, data = -1, b""
        for position in positions:
            block_index, sample_index = divmod(position, _BLOCK_SAMPLES)
            if block_index != previous_block:
                data = _decode_block(self._blocks[block_index])
                previous_block = block_index
            yield BrushSample(*_SAMPLE.unpack_from(data, sample_index * _SAMPLE.size))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CompactBrushSamples):
            return NotImplemented
        if self is other:
            return True
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))

    def __hash__(self) -> int:
        value = 0
        for sample in self:
            value = (value * 31 + hash(sample)) & 0xFFFFFFFFFFFFFFFF
        return hash(value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(count={len(self)})"


class CompactBrushSamplesBuilder:
    __slots__ = ("_blocks", "_pending", "_count")

    def __init__(self) -> None:
        self._blocks: list[bytes] = []
        self._pending = bytearray(_BLOCK_BYTES)
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def append(self, sample: BrushSample) -> None:
        offset = self._count % _BLOCK_SAMPLES * _SAMPLE.size
        _SAMPLE.pack_into(
            self._pending,
            offset,
            sample.x,
            sample.y,
            sample.pressure,
            sample.timestamp_ms,
            sample.x_tilt,
            sample.y_tilt,
            sample.rotation,
            sample.tangential_pressure,
            sample.speed,
        )
        self._count += 1
        if self._count % _BLOCK_SAMPLES == 0:
            self._blocks.append(_encode_block(bytes(self._pending)))

    def finish(self) -> CompactBrushSamples:
        count = self._count % _BLOCK_SAMPLES
        if count:
            self._blocks.append(_encode_block(bytes(self._pending[: count * _SAMPLE.size])))
        samples = CompactBrushSamples._from_blocks(tuple(self._blocks), self._count)
        self._blocks.clear()
        self._count = 0
        return samples
