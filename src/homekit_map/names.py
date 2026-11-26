# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Names of accessories and of the tiles the Home app shows for them.

Each service of an accessory is a tile. Renaming a tile often renames only
the service, so the name a person knows can live on the service while the
accessory keeps the name its maker gave it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from homekit_map.schema import HIDDEN_SERVICE_TYPES, column_or_null, model_key, table_columns, text_or_none


@dataclass(frozen=True)
class Service:
    pk: int
    accessory_pk: int
    instance_id: int | None
    user_name: str | None
    provided_name: str | None
    visible: bool
    key: str

    @property
    def label(self) -> str | None:
        return self.user_name or self.provided_name

    @property
    def renamed(self) -> bool:
        return self.user_name is not None and self.user_name != self.provided_name


@dataclass(frozen=True)
class AccessoryName:
    pk: int
    label: str
    key: str
    model_id: bytes | None
    host_pk: int | None


@dataclass(frozen=True)
class Names:
    accessories: dict[int, AccessoryName]
    services: dict[int, Service]
    tiles: dict[int, tuple[Service, ...]]
    by_model: dict[bytes, int]
    hosted: dict[int, int]

    def accessory(self, pk: int | None) -> str | None:
        found = self.accessories.get(pk) if pk is not None else None
        return found.label if found else None

    def service(self, pk: int | None) -> Service | None:
        return self.services.get(pk) if pk is not None else None

    def tile(self, service_pk: int | None, accessory_pk: int | None) -> str | None:
        """The name of one tile: its own name when it has one people would know, else the accessory's."""
        service = self.service(service_pk)
        if service is not None and service.visible and service.label:
            if service.renamed or len(self.tiles.get(service.accessory_pk, ())) > 1:
                return service.label
            accessory_pk = service.accessory_pk
        return self.accessory(accessory_pk)

    def tile_names(self, accessory_pk: int) -> tuple[str, ...]:
        names: list[str] = []
        for service in self.tiles.get(accessory_pk, ()):
            if service.label and service.label not in names:
                names.append(service.label)
        return tuple(names)

    def service_at(self, accessory_pk: int, instance_id: object) -> Service | None:
        for service in self.services.values():
            if service.accessory_pk == accessory_pk and service.instance_id == instance_id:
                return service
        return None


def load_names(connection: sqlite3.Connection) -> Names:
    accessory_columns = table_columns(connection, "ZMKFACCESSORY")
    accessories: dict[int, AccessoryName] = {}
    by_model: dict[bytes, int] = {}
    hosted: dict[int, int] = {}
    rows = connection.execute(
        f"""
        SELECT a.Z_PK AS pk, a.ZCONFIGUREDNAME AS configured, a.ZPROVIDEDNAME AS provided,
               {column_or_null(accessory_columns, 'a', 'ZMODELID')} AS model,
               {column_or_null(accessory_columns, 'a', 'ZHOSTACCESSORY')} AS host
        FROM ZMKFACCESSORY a
        """
    ).fetchall()
    for row in rows:
        pk = int(row["pk"])
        model = row["model"] if isinstance(row["model"], bytes) else None
        host = int(row["host"]) if row["host"] is not None else None
        label = text_or_none(row["configured"]) or text_or_none(row["provided"]) or "(unnamed)"
        accessories[pk] = AccessoryName(pk, label, model_key(model, pk), model, host)
        if model:
            by_model[model] = pk
        if host is not None:
            hosted[host] = hosted.get(host, 0) + 1
    services = _services(connection)
    tiles: dict[int, list[Service]] = {}
    for service in services.values():
        if service.visible and service.label:
            tiles.setdefault(service.accessory_pk, []).append(service)
    return Names(
        accessories=accessories,
        services=services,
        tiles={pk: tuple(sorted(items, key=lambda item: item.pk)) for pk, items in tiles.items()},
        by_model=by_model,
        hosted=hosted,
    )


def _services(connection: sqlite3.Connection) -> dict[int, Service]:
    columns = table_columns(connection, "ZMKFSERVICE")
    rows = connection.execute(
        f"""
        SELECT s.Z_PK AS pk, s.ZACCESSORY AS accessory, s.ZNAME AS name,
               {column_or_null(columns, 's', 'ZEXPECTEDCONFIGUREDNAME')} AS expected,
               {column_or_null(columns, 's', 'ZPROVIDEDNAME')} AS provided,
               {column_or_null(columns, 's', 'ZINSTANCEID')} AS instance,
               {column_or_null(columns, 's', 'ZSERVICETYPE')} AS kind,
               {column_or_null(columns, 's', 'ZMODELID')} AS model
        FROM ZMKFSERVICE s
        WHERE s.ZACCESSORY IS NOT NULL
        """
    ).fetchall()
    found: dict[int, Service] = {}
    for row in rows:
        pk = int(row["pk"])
        found[pk] = Service(
            pk=pk,
            accessory_pk=int(row["accessory"]),
            instance_id=int(row["instance"]) if row["instance"] is not None else None,
            user_name=text_or_none(row["name"]) or text_or_none(row["expected"]),
            provided_name=text_or_none(row["provided"]),
            visible=row["kind"] not in HIDDEN_SERVICE_TYPES,
            key=model_key(row["model"], pk),
        )
    return found
