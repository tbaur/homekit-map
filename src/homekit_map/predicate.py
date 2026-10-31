# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Read an Eve condition from a trigger's evaluation predicate.

The condition is an NSPredicate archive. A characteristic test is two
comparisons: which characteristic, then the value it must have. The
automation runs only when every test holds, so a false power state means
the automation does not run while that switch is on.
"""

from __future__ import annotations

import plistlib
import sqlite3
import uuid
from dataclasses import dataclass

from homekit_map.schema import column_exists

_RELATIONS = {
    0: "is less than",
    1: "is at most",
    2: "is greater than",
    3: "is at least",
    4: "is",
    5: "is not",
}
_EQUAL_TO = 4

# NSCompoundPredicateType values.
_NOT = 0
_AND = 1
_OR = 2


@dataclass(frozen=True)
class PredicateHit:
    """One characteristic test, plus the accessory it names when known."""

    text: str
    accessory_pk: int | None
    detail: str


def predicate_hits(connection: sqlite3.Connection, blob: bytes | None) -> tuple[PredicateHit, ...]:
    """Return the characteristic tests in one evaluation predicate."""
    root = _root(blob)
    if root is None:
        return ()
    return tuple(_hits(connection, root))


def _hits(connection: sqlite3.Connection, node: object) -> list[PredicateHit]:
    if not isinstance(node, dict):
        return []
    name = _classname(node)
    if name == "NSCompoundPredicate":
        children = _array(node.get("NSSubpredicates"))
        direct = [child for child in children if _classname(child) == "NSComparisonPredicate"]
        nested = [child for child in children if _classname(child) != "NSComparisonPredicate"]
        found = _paired(connection, direct)
        for child in nested:
            found.extend(_hits(connection, child))
        kind = _int(node.get("NSCompoundPredicateType"), _AND)
        if kind == _OR and len(found) > 1:
            return [_joined(found, " or ")]
        if kind == _NOT and found:
            return [_negated(_joined(found, " and ") if len(found) > 1 else found[0])]
        return found
    if name == "NSComparisonPredicate":
        return _paired(connection, [node])
    return []


def _paired(connection: sqlite3.Connection, comparisons: list[dict]) -> list[PredicateHit]:
    identities: list[dict] = []
    values: list[tuple[str, object]] = []
    for comparison in comparisons:
        key = _keypath(comparison.get("NSLeftExpression"))
        value = _dictionary(_constant(comparison.get("NSRightExpression")))
        relation = _relation(comparison.get("NSPredicateOperator"))
        if key == "characteristic" and isinstance(value, dict) and "kAccessoryUUID" in value:
            identities.append(value)
        elif key == "characteristicValue":
            values.append((relation, value))
    found: list[PredicateHit] = []
    for identity, (relation, value) in zip(identities, values, strict=False):
        found.append(_characteristic_hit(connection, identity, relation, value))
    return found


def _characteristic_hit(
    connection: sqlite3.Connection, identity: dict, relation: str, value: object
) -> PredicateHit:
    located = _locate(
        connection,
        str(identity.get("kAccessoryUUID") or ""),
        identity.get("kServiceInstanceID"),
        identity.get("kCharacteristicInstanceID"),
    )
    if located is None:
        shown = _value_text(value, None)
        detail = f"characteristic {relation} {shown}"
        return PredicateHit(detail, None, detail)
    accessory_pk, accessory, _service, characteristic, fmt = located
    shown = _value_text(value, fmt)
    subject = characteristic or "value"
    detail = f"{subject} {relation} {shown}"
    return PredicateHit(f"{accessory}  {detail}", accessory_pk, detail)


def _joined(hits: list[PredicateHit], joiner: str) -> PredicateHit:
    pks = {hit.accessory_pk for hit in hits}
    accessory_pk = next(iter(pks)) if len(pks) == 1 else None
    return PredicateHit(joiner.join(hit.text for hit in hits), accessory_pk, joiner.join(hit.detail for hit in hits))


def _negated(hit: PredicateHit) -> PredicateHit:
    return PredicateHit(f"not ({hit.text})", hit.accessory_pk, f"not ({hit.detail})")


def _int(value: object, default: int) -> int:
    """An archived integer. 0 is a real value here, so it must not fall back to the default."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _locate(
    connection: sqlite3.Connection, accessory_uuid: str, service_instance: object, characteristic_instance: object
) -> tuple[int, str, str | None, str | None, str | None] | None:
    if not column_exists(connection, "ZMKFACCESSORY", "ZMODELID"):
        return None
    try:
        model_id = uuid.UUID(accessory_uuid).bytes
    except ValueError:
        return None
    row = connection.execute(
        """
        SELECT a.Z_PK AS accessory_pk,
               COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '(unnamed)') AS accessory,
               COALESCE(NULLIF(s.ZEXPECTEDCONFIGUREDNAME, ''), s.ZNAME) AS service,
               c.ZMANUFACTURERDESCRIPTION AS characteristic,
               c.ZFORMAT AS format
        FROM ZMKFACCESSORY a
        LEFT JOIN ZMKFSERVICE s
          ON s.ZACCESSORY = a.Z_PK AND s.ZINSTANCEID = ?
        LEFT JOIN ZMKFCHARACTERISTIC c
          ON c.ZSERVICE = s.Z_PK AND c.ZINSTANCEID = ?
        WHERE a.ZMODELID = ?
        """,
        (service_instance, characteristic_instance, model_id),
    ).fetchone()
    if row is None:
        return None
    return int(row["accessory_pk"]), str(row["accessory"]), row["service"], row["characteristic"] or None, row["format"]


def _value_text(value: object, fmt: str | None) -> str:
    if fmt == "bool" or isinstance(value, bool):
        return "true" if value in (True, 1, 1.0) else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _relation(node: object) -> str:
    if not isinstance(node, dict):
        return "is"
    relation = _RELATIONS.get(_int(node.get("NSOperatorType"), _EQUAL_TO), "is")
    if node.get("NSNegate") and relation == "is":
        return "is not"
    if node.get("NSNegate") and relation == "is not":
        return "is"
    return relation


def _dictionary(value: object) -> object:
    """Turn an archived NSDictionary into a plain dict. Other values stay put."""
    if isinstance(value, dict) and isinstance(value.get("NS.keys"), list) and isinstance(value.get("NS.objects"), list):
        return dict(zip(value["NS.keys"], value["NS.objects"], strict=False))
    return value


def _constant(node: object) -> object:
    if isinstance(node, dict) and "NSConstantValue" in node:
        return node["NSConstantValue"]
    return node


def _keypath(node: object) -> str | None:
    if isinstance(node, dict):
        if "NSKeyPath" in node:
            return str(node["NSKeyPath"])
        for value in node.values():
            found = _keypath(value)
            if found:
                return found
    if isinstance(node, list):
        for value in node:
            found = _keypath(value)
            if found:
                return found
    return None


def _array(node: object) -> list:
    if isinstance(node, dict) and isinstance(node.get("NS.objects"), list):
        return list(node["NS.objects"])
    if isinstance(node, list):
        return node
    return []


def _classname(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    kind = node.get("$class")
    if isinstance(kind, dict):
        return str(kind.get("$classname") or "")
    return ""


def _root(blob: bytes | None) -> dict | None:
    if not blob:
        return None
    try:
        loaded = plistlib.loads(blob)
    except (plistlib.InvalidFileException, ValueError, TypeError):
        return None
    if not isinstance(loaded, dict) or loaded.get("$archiver") != "NSKeyedArchiver":
        return None
    objects = loaded.get("$objects")
    top = loaded.get("$top")
    if not isinstance(objects, list) or not isinstance(top, dict):
        return None
    try:
        resolved = _resolve(objects, top.get("root"), frozenset(), 0)
    except _Unreadable:
        return None
    return resolved if isinstance(resolved, dict) else None


class _Unreadable(Exception):
    """The archive points outside itself, loops, or nests too deep."""


_MAX_DEPTH = 64


def _resolve(objects: list, node: object, path: frozenset[int], depth: int) -> object:
    if depth > _MAX_DEPTH:
        raise _Unreadable
    if isinstance(node, plistlib.UID):
        index = node.data
        if index in path or not 0 <= index < len(objects):
            raise _Unreadable
        return _resolve(objects, objects[index], path | {index}, depth + 1)
    if isinstance(node, dict):
        return {key: _resolve(objects, value, path, depth + 1) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve(objects, value, path, depth + 1) for value in node]
    return node
