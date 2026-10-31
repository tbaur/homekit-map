# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Answer which automations and scenes use an accessory.

Reads the snapshot only. A renamed accessory matches either its current
HomeKit name or the name it was given originally.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

from homekit_map.day import trigger_headings
from homekit_map.decode import decode_target
from homekit_map.predicate import PredicateHit, predicate_hits
from homekit_map.schema import (
    StoreSchema,
    column_exists,
    is_active,
    load_schema,
    quote_ident,
    table_columns,
    table_exists,
    text_or_none,
)

_USER_SCENE = "HMActionSetTypeUserDefined"

_CAMERA_BULLETINS = {
    "MKFCameraAccessModeBulletinRegistration": ("ZACCESSORY1", "camera access mode"),
    "MKFCameraReachabilityBulletinRegistration": ("ZACCESSORY2", "camera reachability"),
    "MKFCameraSignificantEventBulletinRegistration": ("ZACCESSORY3", "camera activity"),
}


@dataclass(frozen=True)
class Use:
    """One place an accessory is written or watched.

    automation_pk tells apart automations that share a name. scene names the
    scene an automation runs when the write comes from that scene.
    """

    role: str
    name: str
    enabled: bool | None
    service: str | None
    characteristic: str | None
    target: str | None
    also: tuple[str, ...] = ()
    automation_pk: int | None = None
    scene: str | None = None


@dataclass(frozen=True)
class Accessory:
    """An accessory and the automations, scenes, and triggers that name it."""

    pk: int
    configured_name: str | None
    provided_name: str | None
    manufacturer: str | None
    model: str | None
    room: str | None
    services: tuple[str, ...]
    automations: tuple[Use, ...]
    scenes: tuple[Use, ...]
    triggers: tuple[Use, ...]
    notifications: tuple[str, ...] = ()
    conditions: tuple[Use, ...] = ()

    @property
    def display_name(self) -> str:
        return self.configured_name or self.provided_name or "(unnamed)"

    def match_names(self) -> tuple[str, ...]:
        values = [
            self.configured_name,
            self.provided_name,
            self.manufacturer,
            self.model,
            *self.services,
        ]
        return tuple(value for value in values if value)


@dataclass(frozen=True)
class _Condition:
    trigger_pk: int
    name: str
    enabled: bool
    hits: tuple[PredicateHit, ...]


@dataclass(frozen=True)
class _Shortcut:
    data: bytes
    trigger_pk: int | None
    name: str
    enabled: bool | None


@dataclass(frozen=True)
class _Context:
    """What every accessory lookup needs, read once per command."""

    schema: StoreSchema
    known_names: tuple[bytes, ...]
    headings: dict[int, str]
    conditions: tuple[_Condition, ...]
    shortcuts: tuple[_Shortcut, ...]
    writers: dict[int, tuple[tuple[int, str], ...]]
    bulletin_columns: frozenset[str]


def find_accessories(
    connection: sqlite3.Connection, text: str, scope: str | None = None
) -> list[Accessory]:
    """Return accessories whose names contain text, with their uses filled in."""
    needle = text.casefold().strip()
    if not needle:
        return []
    matched = [
        item for item in _load_accessories(connection) if _matches(item, needle, scope)
    ]
    if not matched:
        return []
    context = _load_context(connection)
    return [_with_uses(connection, context, item) for item in matched]


def all_accessories(connection: sqlite3.Connection) -> list[Accessory]:
    """Return every accessory, including ones no automation uses."""
    context = _load_context(connection)
    return [_with_uses(connection, context, item) for item in _load_accessories(connection)]


def format_accessories(items: list[Accessory]) -> str:
    """Plain text for a terminal. Stable enough to compare."""
    lines = [f"accessories: {len(items)}"]
    for index, item in enumerate(items, start=1):
        lines.append("")
        lines.append(f"accessory {index}: {item.display_name}")
        if item.configured_name and item.configured_name != item.display_name:
            lines.append(f"  configured: {item.configured_name}")
        if item.provided_name and item.provided_name != item.display_name:
            lines.append(f"  provided: {item.provided_name}")
        if item.room:
            lines.append(f"  room: {item.room}")
        device = " ".join(part for part in (item.manufacturer, item.model) if part)
        if device:
            lines.append(f"  device: {device}")
        _append_uses(lines, "automations", item.automations)
        _append_uses(lines, "scenes", item.scenes)
        _append_uses(lines, "triggers", item.triggers)
        if item.notifications:
            lines.append("  notifications:")
            lines.extend(f"    {note}" for note in item.notifications)
        else:
            lines.append("  notifications: none")
    return "\n".join(lines) + "\n"


def _matches(item: Accessory, needle: str, scope: str | None = None) -> bool:
    if scope == "maker":
        fields = (item.manufacturer, item.model)
    elif scope == "accessory":
        fields = (item.configured_name, item.provided_name, *item.services)
    else:
        fields = item.match_names()
    return any(needle in name.casefold() for name in fields if name)


def _append_uses(lines: list[str], heading: str, uses: tuple[Use, ...]) -> None:
    if not uses:
        lines.append(f"  {heading}: none")
        return
    lines.append(f"  {heading}:")
    grouped: dict[tuple[str, bool | None, int | None], list[Use]] = {}
    for use in uses:
        grouped.setdefault((use.name, use.enabled, use.automation_pk), []).append(use)
    for group in grouped.values():
        lines.append(f"    {_use_heading(group[0])}")
        for use in sorted(group, key=_use_order):
            lines.append(f"      {_use_detail(use)}")
        also = list(dict.fromkeys(name for use in group for name in use.also))
        if also:
            lines.append("      also:")
            lines.extend(f"        {name}" for name in also)


def _use_heading(use: Use) -> str:
    if use.enabled is True:
        return f"{use.name} [enabled]"
    if use.enabled is False:
        return f"{use.name} [disabled]"
    return use.name


def _use_order(use: Use) -> int:
    """Eve's order: what starts it, the test, then the scene or accessories."""
    return {
        "trigger": 0,
        "condition": 1,
        "scene": 2,
        "action": 3,
        "adaptive lighting": 4,
        "shortcut": 5,
    }.get(use.role, 9)


def _use_detail(use: Use) -> str:
    if use.role == "trigger":
        return f"trigger  {use.target}" if use.target else "trigger"
    if use.role == "condition":
        return f"condition  {use.target}" if use.target else "condition"
    if use.role == "adaptive lighting":
        return f"adaptive lighting {use.target or 'off'}"
    parts = [f"scene {use.scene}" if use.role == "scene" and use.scene else use.role]
    if use.service:
        parts.append(f"service={use.service}")
    if use.characteristic:
        parts.append(f"characteristic={use.characteristic}")
    if use.target:
        parts.append(f"target={use.target}")
    return "  ".join(parts)


def _load_accessories(connection: sqlite3.Connection) -> list[Accessory]:
    rows = connection.execute(
        """
        SELECT a.Z_PK, a.ZCONFIGUREDNAME, a.ZPROVIDEDNAME, a.ZMANUFACTURER, a.ZMODEL,
               r.ZNAME AS room
        FROM ZMKFACCESSORY a
        LEFT JOIN ZMKFROOM r ON r.Z_PK = a.ZROOM
        ORDER BY COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '')
        """
    ).fetchall()
    services = _services_by_accessory(connection)
    return [
        Accessory(
            pk=int(row["Z_PK"]),
            configured_name=text_or_none(row["ZCONFIGUREDNAME"]),
            provided_name=text_or_none(row["ZPROVIDEDNAME"]),
            manufacturer=text_or_none(row["ZMANUFACTURER"]),
            model=text_or_none(row["ZMODEL"]),
            room=text_or_none(row["room"]),
            services=services.get(int(row["Z_PK"]), ()),
            automations=(),
            scenes=(),
            triggers=(),
        )
        for row in rows
    ]


def _services_by_accessory(connection: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    grouped: dict[int, list[str]] = {}
    rows = connection.execute(
        """
        SELECT ZACCESSORY, ZNAME, ZEXPECTEDCONFIGUREDNAME
        FROM ZMKFSERVICE
        WHERE ZACCESSORY IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        name = text_or_none(row["ZEXPECTEDCONFIGUREDNAME"]) or text_or_none(row["ZNAME"])
        if not name:
            continue
        names = grouped.setdefault(int(row["ZACCESSORY"]), [])
        if name not in names:
            names.append(name)
    return {pk: tuple(names) for pk, names in grouped.items()}


def _load_context(connection: sqlite3.Connection) -> _Context:
    schema = load_schema(connection)
    bulletins = (
        frozenset(table_columns(connection, "ZMKFBULLETINREGISTRATION"))
        if table_exists(connection, "ZMKFBULLETINREGISTRATION")
        else frozenset()
    )
    return _Context(
        schema=schema,
        known_names=_known_names(connection),
        headings=trigger_headings(connection),
        conditions=_load_conditions(connection),
        shortcuts=_load_shortcuts(connection, schema),
        writers=_load_writers(connection, schema),
        bulletin_columns=bulletins,
    )


def _with_uses(connection: sqlite3.Connection, context: _Context, item: Accessory) -> Accessory:
    automations, scenes = _action_uses(connection, context, item.pk)
    shortcut_automations, shortcut_scenes = _shortcut_mentions(context, item)
    automations = _dedupe(automations + shortcut_automations + _condition_uses(context, item.pk))
    return replace(
        item,
        automations=_with_trigger_lines(context, automations),
        scenes=_dedupe(scenes + shortcut_scenes),
        triggers=_trigger_uses(connection, context, item.pk),
        notifications=_notifications(connection, context, item.pk),
    )


def _companions(context: _Context, trigger_pk: int | None, accessory_pk: int) -> tuple[str, ...]:
    """Other accessories the same automation writes."""
    if trigger_pk is None:
        return ()
    labels = {label for pk, label in context.writers.get(trigger_pk, ()) if pk != accessory_pk}
    return tuple(sorted(labels))


def _load_writers(connection: sqlite3.Connection, schema: StoreSchema) -> dict[int, tuple[tuple[int, str], ...]]:
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    rows = connection.execute(
        f"""
        SELECT DISTINCT jt.{trigger_column} AS trigger_pk,
               acc.Z_PK AS accessory_pk,
               COALESCE(NULLIF(acc.ZCONFIGUREDNAME, ''), acc.ZPROVIDEDNAME) AS label
        FROM ZMKFACTION a
        JOIN ZMKFACCESSORY acc
          ON acc.Z_PK = COALESCE(a.ZACCESSORY1, a.ZACCESSORY2, a.ZACCESSORY)
        JOIN {junction} jt ON jt.{action_column} = a.ZACTIONSET
        """
    ).fetchall()
    grouped: dict[int, list[tuple[int, str]]] = {}
    for row in rows:
        if row["trigger_pk"] is None or not row["label"]:
            continue
        grouped.setdefault(int(row["trigger_pk"]), []).append((int(row["accessory_pk"]), str(row["label"])))
    return {pk: tuple(items) for pk, items in grouped.items()}


def _load_conditions(connection: sqlite3.Connection) -> tuple[_Condition, ...]:
    if not column_exists(connection, "ZMKFTRIGGER", "ZEVALUATIONCONDITION"):
        return ()
    rows = connection.execute(
        """
        SELECT Z_PK, ZCONFIGUREDNAME, ZNAME, ZACTIVE, ZEVALUATIONCONDITION
        FROM ZMKFTRIGGER
        WHERE ZEVALUATIONCONDITION IS NOT NULL
        """
    ).fetchall()
    found: list[_Condition] = []
    for row in rows:
        blob = row["ZEVALUATIONCONDITION"]
        hits = predicate_hits(connection, blob if isinstance(blob, bytes) else None)
        if hits:
            found.append(
                _Condition(
                    trigger_pk=int(row["Z_PK"]),
                    name=_trigger_label(row["ZCONFIGUREDNAME"], row["ZNAME"]),
                    enabled=is_active(row["ZACTIVE"]),
                    hits=hits,
                )
            )
    return tuple(found)


def _condition_uses(context: _Context, accessory_pk: int) -> tuple[Use, ...]:
    return tuple(
        Use(
            role="condition",
            name=condition.name,
            enabled=condition.enabled,
            service=None,
            characteristic=None,
            target=hit.detail,
            also=_companions(context, condition.trigger_pk, accessory_pk),
            automation_pk=condition.trigger_pk,
        )
        for condition in context.conditions
        for hit in condition.hits
        if hit.accessory_pk == accessory_pk
    )


def _with_trigger_lines(context: _Context, uses: tuple[Use, ...]) -> tuple[Use, ...]:
    """Put the start of each automation on that automation."""
    seen: set[int] = set()
    extra: list[Use] = []
    for use in uses:
        if use.automation_pk is None or use.automation_pk in seen:
            continue
        heading = context.headings.get(use.automation_pk)
        if heading is None:
            continue
        seen.add(use.automation_pk)
        extra.append(
            Use(
                role="trigger",
                name=use.name,
                enabled=use.enabled,
                service=None,
                characteristic=None,
                target=heading,
                automation_pk=use.automation_pk,
            )
        )
    return tuple(extra) + uses


def _action_uses(
    connection: sqlite3.Connection, context: _Context, accessory_pk: int
) -> tuple[tuple[Use, ...], tuple[Use, ...]]:
    schema = context.schema
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    rows = connection.execute(
        f"""
        SELECT t.Z_PK AS trigger_pk,
               t.ZCONFIGUREDNAME AS trigger_configured,
               t.ZNAME AS trigger_name,
               t.ZACTIVE AS active,
               aset.ZNAME AS set_name,
               aset.ZTYPE AS set_type,
               s.ZNAME AS service_name,
               s.ZEXPECTEDCONFIGUREDNAME AS service_configured,
               c.ZMANUFACTURERDESCRIPTION AS characteristic,
               c.ZFORMAT AS format,
               c.ZUNITS AS units,
               a.ZTARGETVALUE AS target,
               a.Z_ENT AS entity_id,
               a.ZNATURALLIGHTINGENABLEDFIELD AS lighting
        FROM ZMKFACTION a
        LEFT JOIN ZMKFSERVICE s ON s.Z_PK = a.ZSERVICE
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = a.ZSERVICE AND c.ZINSTANCEID = a.ZCHARACTERISTICID
        JOIN ZMKFACTIONSET aset ON aset.Z_PK = a.ZACTIONSET
        LEFT JOIN {junction} jt ON jt.{action_column} = aset.Z_PK
        LEFT JOIN ZMKFTRIGGER t ON t.Z_PK = jt.{trigger_column}
        WHERE a.ZACCESSORY1 = ? OR a.ZACCESSORY2 = ? OR a.ZACCESSORY = ? OR s.ZACCESSORY = ?
        """,
        (accessory_pk, accessory_pk, accessory_pk, accessory_pk),
    ).fetchall()
    automations: list[Use] = []
    scenes: list[Use] = []
    for row in rows:
        write = _write_use(row, schema)
        set_name = text_or_none(row["set_name"]) or "(unnamed)"
        is_scene = row["set_type"] == _USER_SCENE
        trigger_pk = row["trigger_pk"]
        if is_scene or trigger_pk is None:
            scenes.append(replace(write, name=set_name))
        if trigger_pk is not None:
            automations.append(
                replace(
                    write,
                    role="scene" if is_scene else write.role,
                    name=_trigger_label(row["trigger_configured"], row["trigger_name"]),
                    enabled=is_active(row["active"]),
                    also=_companions(context, int(trigger_pk), accessory_pk),
                    automation_pk=int(trigger_pk),
                    scene=set_name if is_scene else None,
                )
            )
    return _dedupe(tuple(automations)), _dedupe(tuple(scenes))


def _write_use(row: sqlite3.Row, schema: StoreSchema) -> Use:
    """The write itself, before it is placed on an automation or a scene."""
    lighting = schema.label("action", int(row["entity_id"])) == "adaptive lighting"
    if lighting:
        return Use(
            role="adaptive lighting",
            name="",
            enabled=None,
            service=None,
            characteristic=None,
            target="on" if row["lighting"] else "off",
        )
    return Use(
        role="action",
        name="",
        enabled=None,
        service=text_or_none(row["service_configured"]) or text_or_none(row["service_name"]),
        characteristic=text_or_none(row["characteristic"]),
        target=decode_target(row["target"], text_or_none(row["format"]), text_or_none(row["units"])),
    )


def _notifications(connection: sqlite3.Connection, context: _Context, accessory_pk: int) -> tuple[str, ...]:
    columns = context.bulletin_columns
    if not columns:
        return ()
    found: list[str] = []
    if "ZCHARACTERISTIC" in columns:
        rows = connection.execute(
            """
            SELECT ch.ZMANUFACTURERDESCRIPTION AS characteristic
            FROM ZMKFBULLETINREGISTRATION b
            JOIN ZMKFCHARACTERISTIC ch ON ch.Z_PK = b.ZCHARACTERISTIC
            JOIN ZMKFSERVICE s ON s.Z_PK = ch.ZSERVICE
            WHERE s.ZACCESSORY = ?
            """,
            (accessory_pk,),
        ).fetchall()
        for row in rows:
            label = text_or_none(row["characteristic"]) or "characteristic"
            if label not in found:
                found.append(label)
    for class_name, (column, label) in _CAMERA_BULLETINS.items():
        entity = context.schema.classes.get(class_name)
        if entity is None or column not in columns:
            continue
        row = connection.execute(
            f"""
            SELECT 1 FROM ZMKFBULLETINREGISTRATION
            WHERE Z_ENT = ? AND {quote_ident(column)} = ?
            """,
            (entity, accessory_pk),
        ).fetchone()
        if row is not None and label not in found:
            found.append(label)
    return tuple(found)


def _trigger_uses(connection: sqlite3.Connection, context: _Context, accessory_pk: int) -> tuple[Use, ...]:
    rows = connection.execute(
        """
        SELECT t.Z_PK AS trigger_pk,
               t.ZCONFIGUREDNAME AS trigger_configured,
               t.ZNAME AS trigger_name,
               t.ZACTIVE AS active,
               t.Z_ENT AS entity_id,
               s.ZNAME AS service_name,
               s.ZEXPECTEDCONFIGUREDNAME AS service_configured,
               c.ZMANUFACTURERDESCRIPTION AS characteristic
        FROM ZMKFEVENT e
        JOIN ZMKFTRIGGER t ON t.Z_PK = e.ZTRIGGER
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = e.ZSERVICE AND c.ZINSTANCEID = e.ZCHARACTERISTICID
        LEFT JOIN ZMKFSERVICE s ON s.Z_PK = e.ZSERVICE
        WHERE s.ZACCESSORY = ?
        """,
        (accessory_pk,),
    ).fetchall()
    found = [
        Use(
            role=context.schema.label("trigger", int(row["entity_id"])),
            name=_trigger_label(row["trigger_configured"], row["trigger_name"]),
            enabled=is_active(row["active"]),
            service=text_or_none(row["service_configured"]) or text_or_none(row["service_name"]),
            characteristic=text_or_none(row["characteristic"]),
            target=None,
            automation_pk=int(row["trigger_pk"]),
        )
        for row in rows
    ]
    return _dedupe(tuple(found))


def _load_shortcuts(connection: sqlite3.Connection, schema: StoreSchema) -> tuple[_Shortcut, ...]:
    shortcut_entity = schema.classes.get("MKFShortcutAction")
    if shortcut_entity is None:
        return ()
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    rows = connection.execute(
        f"""
        SELECT a.ZDATA AS data,
               t.Z_PK AS trigger_pk,
               t.ZCONFIGUREDNAME AS trigger_configured,
               t.ZNAME AS trigger_name,
               t.ZACTIVE AS active,
               aset.ZNAME AS set_name
        FROM ZMKFACTION a
        JOIN ZMKFACTIONSET aset ON aset.Z_PK = a.ZACTIONSET
        LEFT JOIN {junction} jt ON jt.{action_column} = aset.Z_PK
        LEFT JOIN ZMKFTRIGGER t ON t.Z_PK = jt.{trigger_column}
        WHERE a.Z_ENT = ? AND a.ZDATA IS NOT NULL
        """,
        (shortcut_entity,),
    ).fetchall()
    found: list[_Shortcut] = []
    for row in rows:
        if not isinstance(row["data"], bytes):
            continue
        linked = row["trigger_pk"] is not None
        found.append(
            _Shortcut(
                data=row["data"],
                trigger_pk=int(row["trigger_pk"]) if linked else None,
                name=(
                    _trigger_label(row["trigger_configured"], row["trigger_name"])
                    if linked
                    else text_or_none(row["set_name"]) or "(unnamed)"
                ),
                enabled=is_active(row["active"]) if linked else None,
            )
        )
    return tuple(found)


def _shortcut_mentions(context: _Context, item: Accessory) -> tuple[tuple[Use, ...], tuple[Use, ...]]:
    names = [name.encode() for name in item.match_names()]
    if not names or not context.shortcuts:
        return (), ()
    longer = {name: _longer_names(name, context.known_names) for name in names}
    automations: list[Use] = []
    scenes: list[Use] = []
    for shortcut in context.shortcuts:
        if not any(_name_in_blob(shortcut.data, name, longer[name]) for name in names):
            continue
        use = Use(
            role="shortcut",
            name=shortcut.name,
            enabled=shortcut.enabled,
            service=None,
            characteristic=None,
            target=None,
            automation_pk=shortcut.trigger_pk,
        )
        (automations if shortcut.trigger_pk is not None else scenes).append(use)
    return tuple(automations), tuple(scenes)


def _longer_names(name: bytes, known: tuple[bytes, ...]) -> tuple[bytes, ...]:
    """Known names that begin with name, such as "Outlet A S1" for "Outlet A"."""
    return tuple(other for other in known if len(other) > len(name) and other.startswith(name))


def _name_in_blob(payload: bytes, name: bytes, longer: tuple[bytes, ...] = ()) -> bool:
    """True when name occurs on its own, not as the start of a longer known name."""
    start = 0
    while True:
        index = payload.find(name, start)
        if index < 0:
            return False
        before_ok = index == 0 or not chr(payload[index - 1]).isalnum()
        after = index + len(name)
        after_ok = after >= len(payload) or not chr(payload[after]).isalnum()
        covered = any(payload.startswith(extra, index) for extra in longer)
        if before_ok and after_ok and not covered:
            return True
        start = index + 1


def _trigger_label(configured: object, name: object) -> str:
    return text_or_none(configured) or text_or_none(name) or "(unnamed)"


def _dedupe(uses: tuple[Use, ...]) -> tuple[Use, ...]:
    unique = list(dict.fromkeys(uses))
    return tuple(
        sorted(unique, key=lambda use: (use.name.casefold(), use.automation_pk or 0, use.role, use.service or ""))
    )


def _known_names(connection: sqlite3.Connection) -> tuple[bytes, ...]:
    found: set[str] = set()
    for row in connection.execute("SELECT ZCONFIGUREDNAME, ZPROVIDEDNAME FROM ZMKFACCESSORY"):
        found.update(text for text in map(text_or_none, row) if text)
    for row in connection.execute("SELECT ZNAME, ZEXPECTEDCONFIGUREDNAME FROM ZMKFSERVICE"):
        found.update(text for text in map(text_or_none, row) if text)
    return tuple(name.encode() for name in found)
