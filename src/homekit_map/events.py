# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""What starts an automation, and what turns it off again.

An event trigger has start events and end events. ZENDEVENT marks an end
event, which is the Home app's Turn Off setting, such as after 1 hour.
A timer trigger keeps its time on the trigger row instead of in an event.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from homekit_map.decode import decode_clock, decode_number, decode_target, duration_text, render_value
from homekit_map.names import Names
from homekit_map.schema import StoreSchema, column_or_null, is_active, table_columns, text_or_none

# CoreData stores dates as seconds since 2001-01-01 UTC.
_REFERENCE_EPOCH = 978307200
_WEEKDAYS = 2 | 4 | 8 | 16 | 32
_WEEKENDS = 1 | 64
_DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


@dataclass(frozen=True)
class Event:
    """One start or end of one automation.

    text is the line an automation shows. detail drops the accessory, for the
    accessory's own listing. order and heading are set only for the schedule.
    key does not change when something is renamed, so diff can compare it.
    """

    trigger_pk: int
    automation: str
    enabled: bool
    end: bool
    kind: str
    text: str
    detail: str
    accessory_pk: int | None
    service: str | None
    days: int
    order: tuple[int, int, int, str] | None
    heading: str | None
    key: tuple[object, ...]


def load_events(connection: sqlite3.Connection, schema: StoreSchema, names: Names) -> tuple[Event, ...]:
    events = tuple(_event(schema, names, row) for row in _event_rows(connection))
    return events + _timer_starts(connection, schema)


def days_label(mask: int) -> str:
    """Empty for every day. Sunday is the low bit, and 0 means no weekday limit."""
    if mask in (0, 127):
        return ""
    if mask == _WEEKENDS:
        return "weekends"
    if mask == _WEEKDAYS:
        return "weekdays"
    return " ".join(_DAY_NAMES[bit] for bit in range(7) if mask & (1 << bit))


def sun_text(event: object, seconds: object) -> str:
    name = text_or_none(event) or "sun"
    total = _whole_seconds(seconds)
    if total == 0:
        return name
    direction = "before" if total < 0 else "after"
    return f"{duration_text(abs(total))} {direction} {name}"


def presence_text(value: object) -> str:
    text = text_or_none(value) or ""
    if "NoUser" in text or "NotAtHome" in text:
        return "nobody home"
    if text:
        return "someone home"
    return "presence"


def _event_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    events = table_columns(connection, "ZMKFEVENT")
    characteristics = table_columns(connection, "ZMKFCHARACTERISTIC")
    return connection.execute(
        f"""
        SELECT e.Z_PK AS pk, e.Z_ENT AS entity, e.ZTRIGGER AS trigger_pk, e.ZSERVICE AS service,
               e.ZCHARACTERISTICID AS characteristic_id, e.ZSIGNIFICANTEVENT AS significant,
               e.ZOFFSETSECONDS AS offset_seconds, e.ZDURATION AS duration, e.ZPRESENCETYPE AS presence,
               e.ZFIREDATECOMPONENTS AS clock,
               {column_or_null(events, 'e', 'ZENDEVENT')} AS is_end,
               {column_or_null(events, 'e', 'ZEVENTVALUE')} AS value,
               {column_or_null(events, 'e', 'ZMIN')} AS low,
               {column_or_null(events, 'e', 'ZMAX')} AS high,
               t.ZRECURRENCEDAYS AS days, t.ZCONFIGUREDNAME AS trigger_configured, t.ZNAME AS trigger_name,
               t.ZACTIVE AS active,
               c.ZMANUFACTURERDESCRIPTION AS characteristic, c.ZFORMAT AS format, c.ZUNITS AS units,
               {column_or_null(characteristics, 'c', 'ZTYPE')} AS characteristic_type,
               {column_or_null(characteristics, 'c', 'ZMINIMUMVALUE')} AS floor,
               {column_or_null(characteristics, 'c', 'ZMAXIMUMVALUE')} AS ceiling,
               {column_or_null(characteristics, 'c', 'ZMINIMUMVALUE1')} AS floor_int,
               {column_or_null(characteristics, 'c', 'ZMAXIMUMVALUE1')} AS ceiling_int
        FROM ZMKFEVENT e
        JOIN ZMKFTRIGGER t ON t.Z_PK = e.ZTRIGGER
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = e.ZSERVICE AND c.ZINSTANCEID = e.ZCHARACTERISTICID
        ORDER BY e.Z_PK
        """
    ).fetchall()


def _event(schema: StoreSchema, names: Names, row: sqlite3.Row) -> Event:
    kind = schema.label("event", int(row["entity"]))
    end = bool(row["is_end"])
    days = int(row["days"] or 0)
    automation = text_or_none(row["trigger_configured"]) or text_or_none(row["trigger_name"]) or "(unnamed)"
    service = names.service(row["service"])
    accessory_pk = service.accessory_pk if service else None
    key = (
        end,
        kind,
        service.key if service else None,
        row["characteristic_id"],
        _hex(row["value"]),
        _hex(row["low"]),
        _hex(row["high"]),
        row["significant"],
        row["offset_seconds"],
        row["duration"],
        row["presence"],
        _hex(row["clock"]),
        0 if end else days,
    )
    heading: str | None = None
    order: tuple[int, int, int, str] | None = None
    if kind == "calendar":
        clock = decode_clock(row["clock"])
        if clock is not None:
            heading = f"{clock[0]:02d}:{clock[1]:02d}"
            order = (0, clock[0], clock[1], automation)
        detail = heading or "a time"
    elif kind == "sun":
        heading = sun_text(row["significant"], row["offset_seconds"])
        order = (1, 0 if row["significant"] == "sunrise" else 1, _whole_seconds(row["offset_seconds"]), automation)
        detail = heading
    elif kind == "presence":
        heading = presence_text(row["presence"])
        order = (2, 0, 0, automation)
        detail = heading
    elif kind == "duration":
        length = duration_text(_whole_seconds(row["duration"])) if _whole_seconds(row["duration"]) > 0 else "a while"
        if end:
            detail = f"after {length}"
        else:
            heading = "duration"
            order = (3, _whole_seconds(row["duration"]), 0, automation)
            detail = f"for {length}"
    elif kind in ("value", "characteristic", "range"):
        detail = _characteristic_detail(kind, row)
    else:
        detail = kind
    subject = names.tile(service.pk, accessory_pk) if service else None
    text = f"{subject}  {detail}" if subject else detail
    if end:
        text = _end_text(kind, text)
        order = None
        heading = None
    elif kind in ("calendar", "sun", "presence") and days_label(days):
        text = f"{text}  {days_label(days)}"
    return Event(
        trigger_pk=int(row["trigger_pk"]),
        automation=automation,
        enabled=is_active(row["active"]),
        end=end,
        kind=kind,
        text=text,
        detail=detail,
        accessory_pk=accessory_pk,
        service=service.label if service else None,
        days=days,
        order=order,
        heading=heading,
        key=key,
    )


def _end_text(kind: str, text: str) -> str:
    if kind == "duration":
        return text
    if kind in ("calendar", "sun"):
        return f"at {text}"
    return f"when {text}"


def _characteristic_detail(kind: str, row: sqlite3.Row) -> str:
    name = text_or_none(row["characteristic"]) or "value"
    fmt = text_or_none(row["format"])
    units = text_or_none(row["units"])
    characteristic_type = row["characteristic_type"] if isinstance(row["characteristic_type"], bytes) else None
    if kind == "value" and isinstance(row["value"], bytes):
        return f"{name} is {decode_target(row['value'], fmt, units, characteristic_type)}"
    if kind == "range":
        return f"{name} {_range(row, fmt, units, characteristic_type)}"
    return f"{name} changes"


def _range(row: sqlite3.Row, fmt: str | None, units: str | None, characteristic_type: bytes | None) -> str:
    """The Home app stores rises above X as X up to the highest value, and drops below X the other way."""
    low = decode_number(row["low"])
    high = decode_number(row["high"])
    floor = _number(row["floor"], row["floor_int"])
    ceiling = _number(row["ceiling"], row["ceiling_int"])

    def shown(number: int | float) -> str:
        return render_value(number, fmt, units, characteristic_type)

    if low is not None and (high is None or (ceiling is not None and high >= ceiling)):
        return f"rises above {shown(low)}"
    if high is not None and (low is None or (floor is not None and low <= floor)):
        return f"drops below {shown(high)}"
    if low is not None and high is not None:
        return f"is between {shown(low)} and {shown(high)}"
    return "changes"


def _timer_starts(connection: sqlite3.Connection, schema: StoreSchema) -> tuple[Event, ...]:
    """A timer trigger fires at its fire date, or at sunrise or sunset with an offset."""
    timer = schema.classes.get("MKFTimerTrigger")
    columns = table_columns(connection, "ZMKFTRIGGER")
    if timer is None or not {"ZFIREDATE", "ZSIGNIFICANTEVENT"} & columns:
        return ()
    rows = connection.execute(
        f"""
        SELECT t.Z_PK AS pk, t.ZCONFIGUREDNAME AS configured, t.ZNAME AS name, t.ZACTIVE AS active,
               {column_or_null(columns, 't', 'ZFIREDATE')} AS fire_date,
               {column_or_null(columns, 't', 'ZSIGNIFICANTEVENT')} AS significant,
               {column_or_null(columns, 't', 'ZSIGNIFICANTEVENTOFFSET')} AS offset
        FROM ZMKFTRIGGER t
        WHERE t.Z_ENT = ?
        """,
        (timer,),
    ).fetchall()
    found: list[Event] = []
    for row in rows:
        significant = text_or_none(row["significant"])
        if significant is not None:
            offset = decode_clock(row["offset"]) if isinstance(row["offset"], bytes) else None
            seconds = (offset[0] * 3600 + offset[1] * 60) if offset and abs(offset[0]) < 48 else 0
            text = sun_text(significant, seconds)
        elif isinstance(row["fire_date"], int | float):
            when = datetime.fromtimestamp(float(row["fire_date"]) + _REFERENCE_EPOCH)
            text = when.strftime("%H:%M")
        else:
            continue
        found.append(
            Event(
                trigger_pk=int(row["pk"]),
                automation=text_or_none(row["configured"]) or text_or_none(row["name"]) or "(unnamed)",
                enabled=is_active(row["active"]),
                end=False,
                kind="timer",
                text=text,
                detail=text,
                accessory_pk=None,
                service=None,
                days=0,
                order=None,
                heading=None,
                key=(False, "timer", significant, row["fire_date"], _hex(row["offset"])),
            )
        )
    return tuple(found)


def _number(real: object, integer: object) -> int | float | None:
    for value in (real, integer):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value
    return None


def _hex(value: object) -> str | None:
    return value.hex() if isinstance(value, bytes) else None


def _whole_seconds(value: object) -> int:
    if isinstance(value, int | float | str) and value:
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0
