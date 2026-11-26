# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""The house schedule: clock, sun, presence, and durations."""

from __future__ import annotations

import sqlite3

from homekit_map.events import Event, days_label, load_events
from homekit_map.names import load_names
from homekit_map.schema import load_schema

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
    """Group timed automations by when they start. A Turn Off time is not a start."""
    events = load_events(connection, load_schema(connection), load_names(connection))
    items = sorted(
        (item for item in events if _on_schedule(item) and _runs_on(item.days, weekday)),
        key=lambda item: item.order or (9, 0, 0, ""),
    )
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
        days = days_label(item.days)
        suffix = f"  {days}" if days else ""
        lines.append(f"    {item.automation} [{state}]{suffix}")
        if item.kind == "duration":
            lines.append(f"      {item.detail}")
    return "\n".join(lines) + "\n"


def _on_schedule(item: Event) -> bool:
    return not item.end and item.order is not None and item.heading is not None


def _runs_on(mask: int, weekday: int | None) -> bool:
    if weekday is None or mask == 0:
        return True
    return bool(mask & weekday)


def _weekday_heading(mask: int) -> str:
    if mask == _WEEKENDS:
        return "weekends"
    if mask == _WEEKDAYS:
        return "weekdays"
    for name, bit in _DAY_BITS:
        if mask == bit:
            return name
    return days_label(mask).lower()
