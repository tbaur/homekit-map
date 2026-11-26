# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Automations, scenes, and what they do, read once from a snapshot.

Search, the unused list, and diff describe an automation the same way, so
they share this reading of the store. Each action and event carries a key
made of ids, which a rename does not change.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from homekit_map.decode import decode_target
from homekit_map.events import Event, load_events
from homekit_map.names import Names, load_names
from homekit_map.predicate import PredicateHit, predicate_hits
from homekit_map.schema import (
    TRIGGER_OWNED,
    StoreSchema,
    column_or_null,
    is_active,
    load_schema,
    model_key,
    quote_ident,
    table_columns,
    text_or_none,
)

_ROLES = {"write": "action"}


@dataclass(frozen=True)
class Trigger:
    pk: int
    key: str
    label: str
    enabled: bool
    kind: str
    condition: bytes | None


@dataclass(frozen=True)
class ActionSet:
    pk: int
    key: str
    name: str | None
    kind: str | None
    triggers: tuple[int, ...]

    @property
    def is_scene(self) -> bool:
        """A scene in the Home app. Automations own the other sets, and those are hidden."""
        return self.kind != TRIGGER_OWNED


@dataclass(frozen=True)
class Action:
    """One thing an action set does to one accessory. A media action becomes one per speaker."""

    pk: int
    set_pk: int
    role: str
    accessory_pk: int | None
    accessory: str | None
    service: str | None
    tile: str | None
    characteristic: str | None
    target: str | None
    data: bytes | None
    key: tuple[object, ...]


@dataclass(frozen=True)
class Model:
    schema: StoreSchema
    names: Names
    triggers: dict[int, Trigger]
    sets: dict[int, ActionSet]
    actions: tuple[Action, ...]
    events: tuple[Event, ...]
    conditions: dict[int, tuple[PredicateHit, ...]]
    by_set: dict[int, tuple[Action, ...]]
    by_trigger: dict[int, tuple[Event, ...]]

    def starts(self, trigger_pk: int) -> tuple[Event, ...]:
        return tuple(event for event in self.by_trigger.get(trigger_pk, ()) if not event.end)

    def ends(self, trigger_pk: int) -> tuple[Event, ...]:
        return tuple(event for event in self.by_trigger.get(trigger_pk, ()) if event.end)

    def sets_of(self, trigger_pk: int) -> tuple[ActionSet, ...]:
        return tuple(item for item in self.sets.values() if trigger_pk in item.triggers)

    def actions_of(self, trigger_pk: int) -> tuple[Action, ...]:
        return tuple(action for item in self.sets_of(trigger_pk) for action in self.by_set.get(item.pk, ()))


def load_model(connection: sqlite3.Connection, names: Names | None = None) -> Model:
    schema = load_schema(connection)
    names = names or load_names(connection)
    triggers = _triggers(connection, schema)
    actions = _actions(connection, schema, names)
    events = load_events(connection, schema, names)
    by_set: dict[int, list[Action]] = {}
    for action in actions:
        by_set.setdefault(action.set_pk, []).append(action)
    by_trigger: dict[int, list[Event]] = {}
    for event in events:
        by_trigger.setdefault(event.trigger_pk, []).append(event)
    conditions = {
        trigger.pk: hits
        for trigger in triggers.values()
        if (hits := predicate_hits(connection, trigger.condition, names))
    }
    return Model(
        schema=schema,
        names=names,
        triggers=triggers,
        sets=_sets(connection, schema),
        actions=actions,
        events=events,
        conditions=conditions,
        by_set={pk: tuple(items) for pk, items in by_set.items()},
        by_trigger={pk: tuple(items) for pk, items in by_trigger.items()},
    )


def _triggers(connection: sqlite3.Connection, schema: StoreSchema) -> dict[int, Trigger]:
    columns = table_columns(connection, "ZMKFTRIGGER")
    rows = connection.execute(
        f"""
        SELECT t.Z_PK AS pk, t.Z_ENT AS entity, t.ZCONFIGUREDNAME AS configured, t.ZNAME AS name,
               t.ZACTIVE AS active,
               {column_or_null(columns, 't', 'ZEVALUATIONCONDITION')} AS condition,
               {column_or_null(columns, 't', 'ZMODELID')} AS model
        FROM ZMKFTRIGGER t
        """
    ).fetchall()
    found: dict[int, Trigger] = {}
    for row in rows:
        pk = int(row["pk"])
        found[pk] = Trigger(
            pk=pk,
            key=model_key(row["model"], pk),
            label=text_or_none(row["configured"]) or text_or_none(row["name"]) or "(unnamed)",
            enabled=is_active(row["active"]),
            kind=schema.label("trigger", int(row["entity"]) if row["entity"] is not None else None),
            condition=row["condition"] if isinstance(row["condition"], bytes) else None,
        )
    return found


def _sets(connection: sqlite3.Connection, schema: StoreSchema) -> dict[int, ActionSet]:
    linked: dict[int, list[int]] = {}
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    trigger_column = quote_ident(schema.trigger_column)
    for row in connection.execute(f"SELECT {action_column}, {trigger_column} FROM {junction}"):
        if row[0] is not None and row[1] is not None:
            linked.setdefault(int(row[0]), []).append(int(row[1]))
    columns = table_columns(connection, "ZMKFACTIONSET")
    rows = connection.execute(
        f"""
        SELECT s.Z_PK AS pk, s.ZNAME AS name, s.ZTYPE AS kind,
               {column_or_null(columns, 's', 'ZMODELID')} AS model
        FROM ZMKFACTIONSET s
        """
    ).fetchall()
    return {
        int(row["pk"]): ActionSet(
            pk=int(row["pk"]),
            key=model_key(row["model"], row["pk"]),
            name=text_or_none(row["name"]),
            kind=text_or_none(row["kind"]),
            triggers=tuple(sorted(linked.get(int(row["pk"]), []))),
        )
        for row in rows
    }


def _actions(connection: sqlite3.Connection, schema: StoreSchema, names: Names) -> tuple[Action, ...]:
    actions = table_columns(connection, "ZMKFACTION")
    characteristics = table_columns(connection, "ZMKFCHARACTERISTIC")
    rows = connection.execute(
        f"""
        SELECT a.Z_PK AS pk, a.Z_ENT AS entity, a.ZACTIONSET AS set_pk,
               COALESCE(a.ZACCESSORY1, a.ZACCESSORY2, a.ZACCESSORY) AS accessory,
               a.ZSERVICE AS service, a.ZCHARACTERISTICID AS characteristic_id, a.ZTARGETVALUE AS target,
               a.ZNATURALLIGHTINGENABLEDFIELD AS lighting, a.ZDATA AS data,
               {column_or_null(actions, 'a', 'ZSTATE')} AS state,
               c.ZMANUFACTURERDESCRIPTION AS characteristic, c.ZFORMAT AS format, c.ZUNITS AS units,
               {column_or_null(characteristics, 'c', 'ZTYPE')} AS characteristic_type
        FROM ZMKFACTION a
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = a.ZSERVICE AND c.ZINSTANCEID = a.ZCHARACTERISTICID
        WHERE a.ZACTIONSET IS NOT NULL
        ORDER BY a.Z_PK
        """
    ).fetchall()
    speakers = _speakers(connection, schema)
    found: list[Action] = []
    for row in rows:
        label = schema.label("action", int(row["entity"]) if row["entity"] is not None else None)
        role = _ROLES.get(label, label)
        if role == "media playback":
            found.extend(_media(row, names, speakers.get(int(row["pk"]), ())))
            continue
        service = names.service(row["service"])
        accessory_pk = int(row["accessory"]) if row["accessory"] is not None else None
        if accessory_pk is None and service is not None:
            accessory_pk = service.accessory_pk
        data = row["data"] if isinstance(row["data"], bytes) else None
        characteristic_type = row["characteristic_type"] if isinstance(row["characteristic_type"], bytes) else None
        if role == "adaptive lighting":
            target: str | None = "on" if row["lighting"] else "off"
        else:
            target = decode_target(
                row["target"], text_or_none(row["format"]), text_or_none(row["units"]), characteristic_type
            )
        found.append(
            Action(
                pk=int(row["pk"]),
                set_pk=int(row["set_pk"]),
                role=role,
                accessory_pk=accessory_pk,
                accessory=names.accessory(accessory_pk),
                service=service.label if service else None,
                tile=names.tile(service.pk if service else None, accessory_pk),
                characteristic=text_or_none(row["characteristic"]),
                target=target,
                data=data,
                key=(
                    role,
                    _accessory_key(names, accessory_pk),
                    service.key if service else None,
                    row["characteristic_id"],
                    _hex(row["target"]),
                    bool(row["lighting"]) if role == "adaptive lighting" else None,
                    hashlib.sha256(data).hexdigest() if data else None,
                    row["state"],
                ),
            )
        )
    return tuple(found)


def _media(row: sqlite3.Row, names: Names, speakers: tuple[int, ...]) -> list[Action]:
    """The play or pause setting is not decoded. HomeKit does not publish what ZSTATE means."""
    return [
        Action(
            pk=int(row["pk"]),
            set_pk=int(row["set_pk"]),
            role="media playback",
            accessory_pk=speaker,
            accessory=names.accessory(speaker),
            service=None,
            tile=names.accessory(speaker),
            characteristic=None,
            target=None,
            data=None,
            key=("media playback", _accessory_key(names, speaker), row["state"]),
        )
        for speaker in (speakers or (None,))
    ]


def _accessory_key(names: Names, pk: int | None) -> str | None:
    found = names.accessories.get(pk) if pk is not None else None
    return found.key if found else None


def _speakers(connection: sqlite3.Connection, schema: StoreSchema) -> dict[int, tuple[int, ...]]:
    if schema.media is None:
        return {}
    link = schema.media
    found: dict[int, list[int]] = {}
    for row in connection.execute(
        f"SELECT {quote_ident(link.action_column)}, {quote_ident(link.accessory_column)} FROM {quote_ident(link.table)}"
    ):
        if row[0] is not None and row[1] is not None:
            found.setdefault(int(row[0]), []).append(int(row[1]))
    return {pk: tuple(sorted(items)) for pk, items in found.items()}


def _hex(value: object) -> str | None:
    return value.hex() if isinstance(value, bytes) else None
