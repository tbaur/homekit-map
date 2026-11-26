# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Turn stored characteristic values into short text.

HomeKit stores a written or watched value in Apple's OPACK encoding. One byte
holds a small value: 0x01 is true, 0x02 is false, 0x04 is null, and 0x08
through 0x2F are the integers 0 to 39. Larger values start with a tag:
0x30 to 0x33 for a 1, 2, 4, or 8 byte integer, 0x35 for a float, 0x36 for a
double, and 0x40 to 0x60 for a short string. Numbers are little-endian.
"""

from __future__ import annotations

import json
import plistlib
import struct
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

_SINGLE_BYTE: dict[int, bool | None] = {0x01: True, 0x02: False, 0x04: None}
_SMALL_INT_FIRST = 0x08
_SMALL_INT_LAST = 0x2F
_INT_SIZES = {0x30: 1, 0x31: 2, 0x32: 4, 0x33: 8}
_FLOAT_LAYOUTS = {0x35: "<f", 0x36: "<d"}
_SHORT_STRING_FIRST = 0x40
_SHORT_STRING_LAST = 0x60

# Apple's HAP characteristics share this UUID tail. The first four bytes are the short type.
_HAP_TAIL = bytes.fromhex("0000100080000026BB765291")
_FLAG = {0: "false", 1: "true"}
# Words for the numbered states of common HAP characteristics, keyed by short type.
_VALUE_NAMES: dict[int, dict[int, str]] = {
    0x0E: {0: "open", 1: "closed", 2: "opening", 3: "closing", 4: "stopped"},
    0x0F: {0: "off", 1: "heating", 2: "cooling"},
    0x1D: {0: "unlocked", 1: "locked", 2: "jammed", 3: "unknown"},
    0x1E: {0: "unlocked", 1: "locked"},
    0x28: {0: "clockwise", 1: "counterclockwise"},
    0x32: {0: "open", 1: "closed"},
    0x33: {0: "off", 1: "heat", 2: "cool", 3: "auto"},
    0x66: {0: "home", 1: "away", 2: "night", 3: "off", 4: "triggered"},
    0x67: {0: "home", 1: "away", 2: "night", 3: "off"},
    0x69: _FLAG,
    0x6A: {0: "closed", 1: "open"},
    0x70: _FLAG,
    0x71: _FLAG,
    0x73: {0: "single press", 1: "double press", 2: "long press"},
    0x76: _FLAG,
    0x79: {0: "normal", 1: "low"},
    0x8F: {0: "not charging", 1: "charging", 2: "not chargeable"},
    0x92: _FLAG,
    0xB0: _FLAG,
    0xB1: {0: "inactive", 1: "idle", 2: "heating", 3: "cooling"},
    0xB2: {0: "auto", 1: "heat", 2: "cool"},
    0xD2: _FLAG,
}
_UNIT_SUFFIXES = {"percentage": "%", "arcdegrees": "°", "celsius": "°C", "lux": " lux"}


@dataclass(frozen=True)
class _Scalar:
    value: bool | int | float | str | None


def decode_target(
    value: bytes | None,
    fmt: str | None = None,
    units: str | None = None,
    kind: bytes | None = None,
) -> str | None:
    """Return a short rendering of a stored value, such as ZTARGETVALUE. kind is the characteristic type."""
    if value is None:
        return None
    scalar = _opack_scalar(value)
    if scalar is None:
        try:
            loaded = plistlib.loads(value)
        except (ValueError, ExpatError):
            return "0x" + value.hex()
        return _render(loaded)
    return render_value(scalar.value, fmt, units, kind)


def decode_number(value: bytes | None) -> int | float | None:
    """The number in a stored value, or None when it holds something else."""
    scalar = _opack_scalar(value) if value else None
    if scalar is None or isinstance(scalar.value, bool) or not isinstance(scalar.value, int | float):
        return None
    return scalar.value


def render_value(
    value: object,
    fmt: str | None = None,
    units: str | None = None,
    kind: bytes | None = None,
) -> str:
    """Words for a known state, true or false for a flag, else the number with its unit."""
    names = _VALUE_NAMES.get(_short_type(kind), {})
    if isinstance(value, int | float) and float(value).is_integer() and int(value) in names:
        return names[int(value)]
    if isinstance(value, bool) or (fmt == "bool" and isinstance(value, int | float)):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _format_number(value, units)
    return _render(value)


def duration_text(seconds: int) -> str:
    """90 seconds, 30 minutes, 1 hour, or 1 hour 30 minutes."""
    if seconds % 60:
        return _plural(seconds, "second")
    hours, minutes = divmod(seconds // 60, 60)
    parts = [_plural(hours, "hour")] if hours else []
    if minutes or not hours:
        parts.append(_plural(minutes, "minute"))
    return " ".join(parts)


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def decode_clock(value: bytes | None) -> tuple[int, int] | None:
    """Return hour and minute from an NSDateComponents archive."""
    if not value:
        return None
    try:
        parsed = plistlib.loads(value)
    except Exception:
        return None
    objects = parsed.get("$objects") if isinstance(parsed, dict) else None
    if not isinstance(objects, list):
        return None
    for obj in objects:
        if isinstance(obj, dict) and "NS.hour" in obj:
            hour = int(obj.get("NS.hour") or 0)
            minute = int(obj.get("NS.minute") or 0)
            return hour, minute
    return None


def _opack_scalar(value: bytes) -> _Scalar | None:
    """The one OPACK scalar that fills value, or None for anything else."""
    if not value:
        return None
    tag, rest = value[0], value[1:]
    if not rest:
        if tag in _SINGLE_BYTE:
            return _Scalar(_SINGLE_BYTE[tag])
        if _SMALL_INT_FIRST <= tag <= _SMALL_INT_LAST:
            return _Scalar(tag - _SMALL_INT_FIRST)
    size = _INT_SIZES.get(tag)
    if size is not None and len(rest) == size:
        return _Scalar(int.from_bytes(rest, "little"))
    layout = _FLOAT_LAYOUTS.get(tag)
    if layout is not None and len(rest) == struct.calcsize(layout):
        return _Scalar(float(struct.unpack(layout, rest)[0]))
    if _SHORT_STRING_FIRST <= tag <= _SHORT_STRING_LAST and len(rest) == tag - _SHORT_STRING_FIRST:
        try:
            return _Scalar(rest.decode("utf-8"))
        except UnicodeDecodeError:
            return None
    return None


def _short_type(kind: bytes | None) -> int:
    if kind is None or len(kind) != 16 or kind[4:] != _HAP_TAIL:
        return -1
    return int.from_bytes(kind[:4], "big")


def _format_number(number: float | int, units: str | None) -> str:
    if isinstance(number, float) and abs(number - round(number)) < 1e-6:
        text = str(int(round(number)))
    elif isinstance(number, float):
        text = f"{number:.4g}" if abs(number) < 10000 else str(round(number))
    else:
        text = str(number)
    return text + _UNIT_SUFFIXES.get(units or "", "")


def _render(value: object) -> str:
    if isinstance(value, bool | int | float | str) or value is None:
        return json.dumps(value)
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except TypeError:
        return str(value)
