# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Turn stored characteristic targets into short text."""

from __future__ import annotations

import json
import plistlib
import struct

# Bool writes show up as single bytes. Night routines use 0x08 and sunset
# routines use 0x09. A smaller set uses 0x01 and 0x02 the same way.
_BOOL_FALSE = {b"\x08", b"\x01"}
_BOOL_TRUE = {b"\x09", b"\x02"}


def decode_target(
    value: bytes | None,
    fmt: str | None = None,
    units: str | None = None,
) -> str | None:
    """Return a short rendering of a ZTARGETVALUE blob."""
    if value is None:
        return None
    if fmt == "bool" or (fmt is None and value in _BOOL_FALSE | _BOOL_TRUE):
        return _decode_bool(value)
    number = _decode_number(value, fmt)
    if number is None:
        try:
            loaded = plistlib.loads(value)
        except Exception:
            return "0x" + value.hex()
        return _render(loaded)
    return _format_number(number, units)


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


def _decode_bool(value: bytes) -> str | None:
    if value in _BOOL_FALSE:
        return "false"
    if value in _BOOL_TRUE:
        return "true"
    number = _decode_number(value, "float")
    if isinstance(number, float | int):
        return "true" if number else "false"
    return "0x" + value.hex()


def _decode_number(value: bytes, fmt: str | None) -> float | int | None:
    if not value:
        return None
    if len(value) == 1 and fmt in {"int", "uint8", "uint16", "uint32", "float"}:
        return value[0]
    tag = value[0]
    rest = value[1:]
    if tag == 0x30 and len(rest) == 1:
        return rest[0]
    if tag == 0x31 and len(rest) == 2:
        return int.from_bytes(rest, "little")
    if tag == 0x35 and len(rest) == 4:
        return struct.unpack("<f", rest)[0]
    if tag == 0x36 and len(rest) == 8:
        return struct.unpack("<d", rest)[0]
    return None


def _format_number(number: float | int, units: str | None) -> str:
    if isinstance(number, float) and abs(number - round(number)) < 1e-6:
        text = str(int(round(number)))
    elif isinstance(number, float):
        text = f"{number:.4g}"
    else:
        text = str(number)
    if units == "percentage":
        return text + "%"
    if units == "arcdegrees":
        return text + "°"
    return text


def _render(value: object) -> str:
    if isinstance(value, bool | int | float | str) or value is None:
        return json.dumps(value)
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except TypeError:
        return str(value)
