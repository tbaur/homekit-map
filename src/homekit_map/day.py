# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""The house schedule: clock, sun, presence, and durations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

from homekit_map.decode import decode_clock
from homekit_map.schema import StoreSchema, is_active, load_schema, text_or_none


@dataclass(frozen=True)
class When:
    order: tuple[int, int, int, str]
    heading: str
    name: str
    enabled: bool
    detail: str
    days: int = 0
    trigger_pk: int | None = None


# Sunday is the low bit. 127 means every day. 0 means the automation is not limited by weekday.
_DAY_BITS = (
    ("sunday", 1),
    ("monday", 2),
    ("tuesday", 4),
    ("wednesday", 8),
    ("thursday", 16),
    ("friday", 32),
    ("saturday", 64),
)
_DAY_ALIASES = {
    "sun": 1,
    "mon": 2,
    "tue": 4,
    "tues": 4,
    "wed": 8,
    "thu": 16,
    "thur": 16,
    "thurs": 16,
    "fri": 32,
    "sat": 64,
}
_WEEKDAYS = 2 | 4 | 8 | 16 | 32
_WEEKENDS = 1 | 64


def parse_weekday(text: str) -> int | None:
    """Return the weekday bitmask for a name, or None when it is not a day."""
    key = text.casefold().strip()
    if key in {"weekend", "weekends"}:
        return _WEEKENDS
    if key in {"weekday", "weekdays"}:
        return _WEEKDAYS
    for name, bit in _DAY_BITS:
        if key == name:
            return bit
    return _DAY_ALIASES.get(key)


def format_day(connection: sqlite3.Connection, weekday: int | None = None) -> str:
    """Group timed automations by when they happen."""
    schema = load_schema(connection)
    items = [item for item in _events(connection, schema) if _runs_on(item.days, weekday)]
    title = "day:" if weekday is None else f"day: {_weekday_heading(weekday)}"
    if not items:
        return f"{title}\n  none\n"
    lines = [title]
    current = None
    for item in items:
        if item.heading != current:
            current = item.heading
            lines.append(f"  {item.heading}")
        state = "enabled" if item.enabled else "disabled"
        days = _days_label(item.days)
        suffix = f"  {days}" if days else ""
        lines.append(f"    {item.name} [{state}]{suffix}")
        if item.detail:
            lines.append(f"      {item.detail}")
    return "\n".join(lines) + "\n"


def _events(connection: sqlite3.Connection, schema: StoreSchema) -> list[When]:
    rows = connection.execute(
        """
        SELECT t.Z_PK AS trigger_pk,
               t.ZCONFIGUREDNAME AS trigger_configured,
               t.ZNAME AS trigger_name,
               t.ZACTIVE AS active,
               e.Z_ENT AS entity_id,
               e.ZSIGNIFICANTEVENT AS significant,
               e.ZOFFSETSECONDS AS offset_seconds,
               e.ZDURATION AS duration,
               e.ZPRESENCETYPE AS presence,
               e.ZFIREDATECOMPONENTS AS clock,
               t.ZRECURRENCEDAYS AS days,
               c.ZMANUFACTURERDESCRIPTION AS characteristic
        FROM ZMKFEVENT e
        JOIN ZMKFTRIGGER t ON t.Z_PK = e.ZTRIGGER
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = e.ZSERVICE AND c.ZINSTANCEID = e.ZCHARACTERISTICID
        """
    ).fetchall()
    found: list[When] = []
    for row in rows:
        when = _when(schema, row)
        if when is not None:
            found.append(replace(when, trigger_pk=int(row["trigger_pk"])))
    return sorted(found, key=lambda item: item.order)


def _when(schema: StoreSchema, row: sqlite3.Row) -> When | None:
    name = text_or_none(row["trigger_configured"]) or text_or_none(row["trigger_name"]) or "(unnamed)"
    enabled = is_active(row["active"])
    kind = schema.label("event", int(row["entity_id"]))
    characteristic = text_or_none(row["characteristic"])
    if kind == "calendar":
        clock = decode_clock(row["clock"])
        if clock is None:
            return None
        hour, minute = clock
        days = int(row["days"] or 0)
        return When((0, hour, minute, name), f"{hour:02d}:{minute:02d}", name, enabled, "", days)
    if kind == "sun":
        heading = _sun(row["significant"], row["offset_seconds"])
        offset = int(row["offset_seconds"] or 0)
        sun = 0 if row["significant"] == "sunrise" else 1
        days = int(row["days"] or 0)
        return When((1, sun, offset, name), heading, name, enabled, "", days)
    if kind == "presence":
        heading = _presence(row["presence"])
        return When((2, 0, 0, name), heading, name, enabled, "", int(row["days"] or 0))
    if kind == "duration":
        detail = _duration(row["duration"])
        if characteristic:
            detail = f"{detail}  characteristic={characteristic}"
        return When(
            (3, int(row["duration"] or 0), 0, name),
            "duration",
            name,
            enabled,
            detail,
            int(row["days"] or 0),
        )
    return None


def trigger_headings(connection: sqlite3.Connection) -> dict[int, str]:
    """The clock, sun, or presence line that starts each automation, by trigger id."""
    schema = load_schema(connection)
    grouped: dict[int, list[str]] = {}
    for item in _events(connection, schema):
        label = item.heading
        days = _days_label(item.days)
        if days:
            label = f"{label}  {days}"
        if item.trigger_pk is None:
            continue
        bucket = grouped.setdefault(item.trigger_pk, [])
        if label not in bucket:
            bucket.append(label)
    return {pk: ", ".join(labels) for pk, labels in grouped.items()}


def _runs_on(mask: int, weekday: int | None) -> bool:
    if weekday is None or mask == 0:
        return True
    return bool(mask & weekday)


def _days_label(mask: int) -> str:
    if mask in (0, 127):
        return ""
    if mask == _WEEKENDS:
        return "weekends"
    if mask == _WEEKDAYS:
        return "weekdays"
    names = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
    return " ".join(names[bit] for bit in range(7) if mask & (1 << bit))


def _weekday_heading(mask: int) -> str:
    if mask == _WEEKENDS:
        return "weekends"
    if mask == _WEEKDAYS:
        return "weekdays"
    for name, bit in _DAY_BITS:
        if mask == bit:
            return name
    return _days_label(mask).lower()


def _sun(event: object, seconds: object) -> str:
    name = text_or_none(event) or "sun"
    total = int(float(seconds or 0))
    if total == 0:
        return name
    direction = "before" if total < 0 else "after"
    amount = abs(total)
    if amount % 60:
        unit = "second" if amount == 1 else "seconds"
    else:
        amount //= 60
        unit = "minute" if amount == 1 else "minutes"
    return f"{amount} {unit} {direction} {name}"


def _presence(value: object) -> str:
    text = text_or_none(value) or ""
    if "NoUser" in text or "NotAtHome" in text:
        return "nobody home"
    if text:
        return "someone home"
    return "presence"


def _duration(seconds: object) -> str:
    total = int(float(seconds or 0))
    if total <= 0:
        return "duration"
    if total % 3600 == 0:
        hours = total // 3600
        unit = "hour" if hours == 1 else "hours"
        return f"for {hours} {unit}"
    if total % 60 == 0:
        minutes = total // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"for {minutes} {unit}"
    return f"for {total} seconds"

