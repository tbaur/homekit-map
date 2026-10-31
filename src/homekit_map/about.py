# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Look up a name wherever it appears in the snapshot.

A name can be an accessory, a service, an automation, a scene, or a room.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from homekit_map.day import trigger_headings
from homekit_map.decode import decode_target
from homekit_map.predicate import predicate_hits
from homekit_map.query import Accessory, find_accessories, format_accessories
from homekit_map.schema import StoreSchema, column_exists, is_active, load_schema, quote_ident, text_or_none


@dataclass(frozen=True)
class Step:
    """One trigger or write inside an automation or scene."""

    accessory: str | None
    service: str | None
    characteristic: str | None
    target: str | None
    kind: str


@dataclass(frozen=True)
class Automation:
    name: str
    enabled: bool
    trigger_type: str
    triggers: tuple[Step, ...]
    actions: tuple[Step, ...]
    conditions: tuple[str, ...] = ()
    trigger_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scene:
    name: str
    actions: tuple[Step, ...]
    used_by: tuple[str, ...]


@dataclass(frozen=True)
class Room:
    name: str
    accessories: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    accessories: tuple[Accessory, ...]
    automations: tuple[Automation, ...]
    scenes: tuple[Scene, ...]
    rooms: tuple[Room, ...]
    kind: str | None = None


_KINDS = {
    "room": "room",
    "rooms": "room",
    "accessory": "accessory",
    "accessories": "accessory",
    "automation": "automation",
    "automations": "automation",
    "scene": "scene",
    "scenes": "scene",
    "day": "day",
    "days": "day",
    "unused": "unused",
}


def split_search(text: str) -> tuple[str | None, str]:
    """A leading kind word limits the lookup. The rest is the name."""
    parts = text.split()
    if parts and parts[0].casefold() in {"day", "days", "unused"}:
        return _KINDS[parts[0].casefold()], " ".join(parts[1:])
    if len(parts) >= 2 and parts[0].casefold() in _KINDS:
        return _KINDS[parts[0].casefold()], " ".join(parts[1:])
    return None, text.strip()


def describe(connection: sqlite3.Connection, text: str) -> Report:
    """Return every object whose name contains text, with what the snapshot knows."""
    kind, needle = split_search(text)
    needle_cf = needle.casefold()
    if not needle_cf:
        return Report((), (), (), (), kind)
    schema = load_schema(connection)
    accessories: tuple[Accessory, ...] = ()
    automations: tuple[Automation, ...] = ()
    scenes: tuple[Scene, ...] = ()
    rooms: tuple[Room, ...] = ()
    if kind in (None, "accessory", "maker"):
        accessories = tuple(find_accessories(connection, needle, kind))
    if kind in (None, "automation"):
        automations = tuple(_matching_automations(connection, schema, needle_cf))
    if kind in (None, "scene"):
        scenes = tuple(_matching_scenes(connection, schema, needle_cf))
    if kind in (None, "room"):
        rooms = tuple(_matching_rooms(connection, needle_cf))
    return Report(accessories, automations, scenes, rooms, kind)


def format_report(report: Report) -> str:
    """Plain text covering every kind of match."""
    if report.kind:
        return _format_one_kind(report)
    lines = [
        f"accessories: {len(report.accessories)}",
        f"automations: {len(report.automations)}",
        f"scenes: {len(report.scenes)}",
        f"rooms: {len(report.rooms)}",
    ]
    if report.accessories:
        lines.append("")
        lines.append(format_accessories(list(report.accessories)).rstrip("\n"))
    for automation in report.automations:
        lines.extend(_automation_lines(automation))
    for scene in report.scenes:
        lines.extend(_scene_lines(scene))
    for room in report.rooms:
        lines.append("")
        lines.append(f"room: {room.name}")
        if room.accessories:
            lines.append("  accessories:")
            lines.extend(f"    {name}" for name in room.accessories)
        else:
            lines.append("  accessories: none")
    return "\n".join(lines) + "\n"


def _format_one_kind(report: Report) -> str:
    """Show only the kind the search asked for."""
    if report.kind == "room":
        if not report.rooms:
            return "rooms: 0\n"
        lines: list[str] = []
        for item in report.rooms:
            lines.append(f"room: {item.name}")
            if item.accessories:
                lines.append("  accessories:")
                lines.extend(f"    {name}" for name in item.accessories)
            else:
                lines.append("  accessories: none")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    if report.kind == "automation":
        if not report.automations:
            return "automations: 0\n"
        lines = []
        for automation in report.automations:
            lines.extend(_automation_lines(automation))
        return "\n".join(lines).lstrip("\n") + "\n"
    if report.kind == "scene":
        if not report.scenes:
            return "scenes: 0\n"
        lines = []
        for scene in report.scenes:
            lines.extend(_scene_lines(scene))
        return "\n".join(lines).lstrip("\n") + "\n"
    if not report.accessories:
        label = "makers" if report.kind == "maker" else "accessories"
        return f"{label}: 0\n"
    return format_accessories(list(report.accessories))


def _automation_lines(item: Automation) -> list[str]:
    state = "enabled" if item.enabled else "disabled"
    lines = ["", f"automation: {item.name}", f"  {state}", f"  type: {item.trigger_type}"]
    if item.trigger_lines:
        lines.append("  trigger:")
        lines.extend(f"    {line}" for line in item.trigger_lines)
        detailed = tuple(step for step in item.triggers if step.accessory or step.characteristic)
        if detailed:
            _append_steps(lines, "triggers", detailed)
    else:
        _append_steps(lines, "triggers", item.triggers)
    if item.conditions:
        lines.append("  condition:")
        lines.extend(f"    {line}" for line in item.conditions)
    _append_steps(lines, "actions", item.actions)
    return lines


def _scene_lines(item: Scene) -> list[str]:
    lines = ["", f"scene: {item.name}"]
    _append_steps(lines, "actions", item.actions)
    if item.used_by:
        lines.append("  used by:")
        lines.extend(f"    {name}" for name in item.used_by)
    else:
        lines.append("  used by: none")
    return lines


def _append_steps(lines: list[str], heading: str, steps: tuple[Step, ...]) -> None:
    if not steps:
        lines.append(f"  {heading}: none")
        return
    lines.append(f"  {heading}:")
    for step in steps:
        if step.kind == "adaptive lighting":
            who = step.accessory or "light"
            state = "on" if step.target == "on" else "off"
            lines.append(f"    {who}  adaptive lighting {state}")
            continue
        if step.accessory:
            detail = [step.accessory]
            if step.kind not in {"write", "action"}:
                detail.append(step.kind)
        else:
            detail = [step.kind]
        if step.service:
            detail.append(f"service={step.service}")
        if step.characteristic:
            detail.append(f"characteristic={step.characteristic}")
        if step.target:
            detail.append(f"target={step.target}")
        lines.append("    " + "  ".join(detail))


def _matching_automations(
    connection: sqlite3.Connection, schema: StoreSchema, needle: str
) -> list[Automation]:
    found: list[Automation] = []
    headings = trigger_headings(connection)
    rows = connection.execute(
        "SELECT Z_PK, Z_ENT, ZCONFIGUREDNAME, ZNAME, ZACTIVE FROM ZMKFTRIGGER"
    ).fetchall()
    for row in rows:
        name = text_or_none(row["ZCONFIGUREDNAME"]) or text_or_none(row["ZNAME"]) or "(unnamed)"
        if needle not in name.casefold():
            continue
        pk = int(row["Z_PK"])
        found.append(
            Automation(
                name=name,
                enabled=is_active(row["ZACTIVE"]),
                trigger_type=schema.label("trigger", int(row["Z_ENT"])),
                triggers=_event_steps(connection, schema, pk),
                actions=_action_steps(connection, schema, pk),
                conditions=_condition_lines(connection, pk),
                trigger_lines=(headings[pk],) if pk in headings else (),
            )
        )
    return sorted(found, key=lambda item: item.name.casefold())


def _matching_scenes(
    connection: sqlite3.Connection, schema: StoreSchema, needle: str
) -> list[Scene]:
    found: list[Scene] = []
    rows = connection.execute(
        "SELECT Z_PK, ZNAME FROM ZMKFACTIONSET WHERE ZNAME IS NOT NULL AND ZNAME != ''"
    ).fetchall()
    for row in rows:
        name = str(row["ZNAME"])
        if needle not in name.casefold():
            continue
        found.append(
            Scene(
                name=name,
                actions=_set_steps(connection, schema, int(row["Z_PK"])),
                used_by=_automations_for_set(connection, schema, int(row["Z_PK"])),
            )
        )
    return sorted(found, key=lambda item: item.name.casefold())


def _matching_rooms(connection: sqlite3.Connection, needle: str) -> list[Room]:
    found: list[Room] = []
    rows = connection.execute("SELECT Z_PK, ZNAME FROM ZMKFROOM").fetchall()
    for row in rows:
        name = text_or_none(row["ZNAME"])
        if name is None or needle not in name.casefold():
            continue
        accessories = connection.execute(
            """
            SELECT COALESCE(NULLIF(ZCONFIGUREDNAME, ''), ZPROVIDEDNAME, '(unnamed)') AS label
            FROM ZMKFACCESSORY
            WHERE ZROOM = ?
            ORDER BY label
            """,
            (int(row["Z_PK"]),),
        ).fetchall()
        found.append(Room(name=name, accessories=tuple(str(item["label"]) for item in accessories)))
    return found


def _condition_lines(connection: sqlite3.Connection, trigger_pk: int) -> tuple[str, ...]:
    if not column_exists(connection, "ZMKFTRIGGER", "ZEVALUATIONCONDITION"):
        return ()
    row = connection.execute(
        "SELECT ZEVALUATIONCONDITION FROM ZMKFTRIGGER WHERE Z_PK = ?",
        (trigger_pk,),
    ).fetchone()
    blob = row["ZEVALUATIONCONDITION"] if row is not None else None
    hits = predicate_hits(connection, blob if isinstance(blob, bytes) else None)
    return tuple(hit.text for hit in hits)


def _event_steps(
    connection: sqlite3.Connection, schema: StoreSchema, trigger_pk: int
) -> tuple[Step, ...]:
    rows = connection.execute(
        """
        SELECT e.Z_ENT AS entity_id,
               COALESCE(NULLIF(acc.ZCONFIGUREDNAME, ''), acc.ZPROVIDEDNAME) AS accessory,
               COALESCE(NULLIF(s.ZEXPECTEDCONFIGUREDNAME, ''), s.ZNAME) AS service,
               c.ZMANUFACTURERDESCRIPTION AS characteristic
        FROM ZMKFEVENT e
        LEFT JOIN ZMKFSERVICE s ON s.Z_PK = e.ZSERVICE
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = e.ZSERVICE AND c.ZINSTANCEID = e.ZCHARACTERISTICID
        LEFT JOIN ZMKFACCESSORY acc ON acc.Z_PK = s.ZACCESSORY
        WHERE e.ZTRIGGER = ?
        """,
        (trigger_pk,),
    ).fetchall()
    return tuple(_step(row, schema, "event") for row in rows)


def _action_steps(
    connection: sqlite3.Connection, schema: StoreSchema, trigger_pk: int
) -> tuple[Step, ...]:
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    rows = connection.execute(
        f"""
        SELECT a.Z_ENT AS entity_id,
               COALESCE(
                   NULLIF(acc.ZCONFIGUREDNAME, ''), acc.ZPROVIDEDNAME
               ) AS accessory,
               COALESCE(NULLIF(s.ZEXPECTEDCONFIGUREDNAME, ''), s.ZNAME) AS service,
               c.ZMANUFACTURERDESCRIPTION AS characteristic,
               c.ZFORMAT AS format,
               c.ZUNITS AS units,
               a.ZTARGETVALUE AS target,
               a.ZNATURALLIGHTINGENABLEDFIELD AS lighting
        FROM {junction} jt
        JOIN ZMKFACTION a ON a.ZACTIONSET = jt.{action_column}
        LEFT JOIN ZMKFACCESSORY acc
          ON acc.Z_PK = COALESCE(a.ZACCESSORY1, a.ZACCESSORY2, a.ZACCESSORY)
        LEFT JOIN ZMKFSERVICE s ON s.Z_PK = a.ZSERVICE
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = a.ZSERVICE AND c.ZINSTANCEID = a.ZCHARACTERISTICID
        WHERE jt.{trigger_column} = ?
        """,
        (trigger_pk,),
    ).fetchall()
    return tuple(_step(row, schema, "action", with_target=True) for row in rows)


def _set_steps(
    connection: sqlite3.Connection, schema: StoreSchema, action_set_pk: int
) -> tuple[Step, ...]:
    rows = connection.execute(
        """
        SELECT a.Z_ENT AS entity_id,
               COALESCE(
                   NULLIF(acc.ZCONFIGUREDNAME, ''), acc.ZPROVIDEDNAME
               ) AS accessory,
               COALESCE(NULLIF(s.ZEXPECTEDCONFIGUREDNAME, ''), s.ZNAME) AS service,
               c.ZMANUFACTURERDESCRIPTION AS characteristic,
               c.ZFORMAT AS format,
               c.ZUNITS AS units,
               a.ZTARGETVALUE AS target,
               a.ZNATURALLIGHTINGENABLEDFIELD AS lighting
        FROM ZMKFACTION a
        LEFT JOIN ZMKFACCESSORY acc
          ON acc.Z_PK = COALESCE(a.ZACCESSORY1, a.ZACCESSORY2, a.ZACCESSORY)
        LEFT JOIN ZMKFSERVICE s ON s.Z_PK = a.ZSERVICE
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = a.ZSERVICE AND c.ZINSTANCEID = a.ZCHARACTERISTICID
        WHERE a.ZACTIONSET = ?
        """,
        (action_set_pk,),
    ).fetchall()
    return tuple(_step(row, schema, "action", with_target=True) for row in rows)


def _automations_for_set(
    connection: sqlite3.Connection, schema: StoreSchema, action_set_pk: int
) -> tuple[str, ...]:
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    rows = connection.execute(
        f"""
        SELECT COALESCE(NULLIF(t.ZCONFIGUREDNAME, ''), t.ZNAME, '(unnamed)') AS label
        FROM {junction} jt
        JOIN ZMKFTRIGGER t ON t.Z_PK = jt.{trigger_column}
        WHERE jt.{action_column} = ?
        ORDER BY label
        """,
        (action_set_pk,),
    ).fetchall()
    return tuple(str(row["label"]) for row in rows)


def _step(
    row: sqlite3.Row, schema: StoreSchema, group: str, *, with_target: bool = False
) -> Step:
    entity = int(row["entity_id"]) if row["entity_id"] is not None else None
    kind = schema.label(group, entity) if entity is not None else group
    target = None
    if kind == "adaptive lighting" and "lighting" in row.keys():
        target = "on" if row["lighting"] else "off"
    elif with_target and "target" in row.keys():
        fmt = row["format"] if "format" in row.keys() else None
        units = row["units"] if "units" in row.keys() else None
        target = decode_target(row["target"], fmt, units)
    return Step(
        accessory=text_or_none(row["accessory"]),
        service=text_or_none(row["service"]),
        characteristic=text_or_none(row["characteristic"]),
        target=target,
        kind=kind,
    )

