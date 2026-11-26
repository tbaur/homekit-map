# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Read an automation's condition from its evaluation predicate.

The condition is an NSPredicate archive. A characteristic test is two
comparisons: which characteristic, then the value it must have. A time test
compares now() with a clock time, sunrise, or sunset, sometimes moved by an
offset. The automation runs only when every test holds, so a false power
state means the automation does not run while that switch is on.
"""

from __future__ import annotations

import plistlib
import sqlite3
import uuid
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

from homekit_map.decode import duration_text, render_value
from homekit_map.names import Names, load_names
from homekit_map.schema import column_exists, column_or_null, table_columns, text_or_none

_RELATIONS = {
    0: "is less than",
    1: "is at most",
    2: "is greater than",
    3: "is at least",
    4: "is",
    5: "is not",
}
_EQUAL_TO = 4
# A time test is read as now <operator> moment. Swapping the two sides flips the operator.
_FLIPPED = {0: 2, 1: 3, 2: 0, 3: 1, 4: 4}
_TIME_WORDS = {0: "before", 1: "before", 2: "after", 3: "after", 4: "at"}
_SUN = ("sunrise", "sunset")
_UNDECODED = "other condition (not decoded)"
# NSDateComponents stores a field that is not set as NSIntegerMax.
_UNSET = 2**63 - 1

# NSCompoundPredicateType values.
_NOT = 0
_AND = 1
_OR = 2


@dataclass(frozen=True)
class PredicateHit:
    """One test, plus the accessory it names when known. bound is (after or before, moment) for a time test."""

    text: str
    accessory_pk: int | None
    detail: str
    bound: tuple[str, str] | None = None


@dataclass(frozen=True)
class _Moment:
    """now, sunrise, sunset, or a clock time, plus minutes. A clock time counts from midnight."""

    base: str
    minutes: int

    def shifted(self, minutes: int) -> _Moment:
        return _Moment(self.base, self.minutes + minutes)

    def text(self) -> str:
        if self.base == "clock":
            total = self.minutes % (24 * 60)
            return f"{total // 60:02d}:{total % 60:02d}"
        if self.minutes == 0:
            return self.base
        direction = "before" if self.minutes < 0 else "after"
        return f"{duration_text(abs(self.minutes) * 60)} {direction} {self.base}"


def predicate_hits(
    connection: sqlite3.Connection, blob: bytes | None, names: Names | None = None
) -> tuple[PredicateHit, ...]:
    """Return the tests in one evaluation predicate."""
    root = _root(blob)
    if root is None:
        return ()
    return tuple(_Reader(connection, names).hits(root))


class _Reader:
    def __init__(self, connection: sqlite3.Connection, names: Names | None) -> None:
        self._connection = connection
        self._names = names
        self._can_locate = column_exists(connection, "ZMKFACCESSORY", "ZMODELID")

    def hits(self, node: object) -> list[PredicateHit]:
        if not isinstance(node, dict):
            return []
        name = _classname(node)
        if name == "NSCompoundPredicate":
            children = _array(node.get("NSSubpredicates"))
            direct = [
                child for child in children if isinstance(child, dict) and _classname(child) == "NSComparisonPredicate"
            ]
            nested = [child for child in children if _classname(child) != "NSComparisonPredicate"]
            found = self._paired(direct)
            for child in nested:
                found.extend(self.hits(child))
            kind = _int(node.get("NSCompoundPredicateType"), _AND)
            window = _window(found) if kind in (_AND, _OR) else None
            if window is not None:
                return [window]
            if kind == _OR and len(found) > 1:
                return [_joined(found, " or ")]
            if kind == _NOT and found:
                return [_negated(_joined(found, " and ") if len(found) > 1 else found[0])]
            return found
        if name == "NSComparisonPredicate":
            return self._paired([node])
        return []

    def _paired(self, comparisons: list[dict[str, object]]) -> list[PredicateHit]:
        identities: list[dict[str, object]] = []
        values: list[tuple[str, object]] = []
        timed: list[PredicateHit] = []
        unknown = 0
        for comparison in comparisons:
            key = _keypath(comparison.get("NSLeftExpression"))
            value = _dictionary(_constant(comparison.get("NSRightExpression")))
            relation = _relation(comparison.get("NSPredicateOperator"))
            if key == "characteristic" and isinstance(value, dict) and "kAccessoryUUID" in value:
                identities.append(value)
            elif key == "characteristicValue":
                values.append((relation, value))
            elif (hit := _time_hit(comparison)) is not None:
                timed.append(hit)
            else:
                unknown += 1
        found = [
            self._characteristic_hit(identity, relation, value)
            for identity, (relation, value) in zip(identities, values, strict=False)
        ]
        found.extend(timed)
        if unknown:
            found.append(PredicateHit(_UNDECODED, None, _UNDECODED))
        return found

    def _characteristic_hit(self, identity: dict[str, object], relation: str, value: object) -> PredicateHit:
        located = self._locate(
            str(identity.get("kAccessoryUUID") or ""),
            identity.get("kServiceInstanceID"),
            identity.get("kCharacteristicInstanceID"),
        )
        if located is None:
            detail = f"characteristic {relation} {render_value(value)}"
            return PredicateHit(detail, None, detail)
        accessory_pk, subject, characteristic, fmt, units, kind = located
        detail = f"{characteristic or 'value'} {relation} {render_value(value, fmt, units, kind)}"
        return PredicateHit(f"{subject}  {detail}", accessory_pk, detail)

    def _locate(
        self, accessory_uuid: str, service_instance: object, characteristic_instance: object
    ) -> tuple[int, str, str | None, str | None, str | None, bytes | None] | None:
        if not self._can_locate:
            return None
        try:
            model_id = uuid.UUID(accessory_uuid).bytes
        except ValueError:
            return None
        if self._names is None:
            self._names = load_names(self._connection)
        names = self._names
        accessory_pk = names.by_model.get(model_id)
        if accessory_pk is None:
            return None
        service = names.service_at(accessory_pk, service_instance)
        subject = names.tile(service.pk if service else None, accessory_pk) or "(unnamed)"
        if service is None:
            return accessory_pk, subject, None, None, None, None
        columns = table_columns(self._connection, "ZMKFCHARACTERISTIC")
        row = self._connection.execute(
            f"""
            SELECT c.ZMANUFACTURERDESCRIPTION AS characteristic, c.ZFORMAT AS format, c.ZUNITS AS units,
                   {column_or_null(columns, 'c', 'ZTYPE')} AS kind
            FROM ZMKFCHARACTERISTIC c
            WHERE c.ZSERVICE = ? AND c.ZINSTANCEID = ?
            """,
            (service.pk, characteristic_instance),
        ).fetchone()
        if row is None:
            return accessory_pk, subject, None, None, None, None
        kind = row["kind"] if isinstance(row["kind"], bytes) else None
        return (
            accessory_pk,
            subject,
            text_or_none(row["characteristic"]),
            text_or_none(row["format"]),
            text_or_none(row["units"]),
            kind,
        )


def _time_hit(comparison: dict[str, object]) -> PredicateHit | None:
    left = _moment(comparison.get("NSLeftExpression"))
    right = _moment(comparison.get("NSRightExpression"))
    if left is None or right is None:
        return None
    operator = _operator(comparison.get("NSPredicateOperator"))
    if left.base == "now" and right.base != "now":
        moment = right.shifted(-left.minutes)
    elif right.base == "now" and left.base != "now":
        moment = left.shifted(-right.minutes)
        operator = _FLIPPED.get(operator, -1)
    else:
        return None
    word = _TIME_WORDS.get(operator)
    if word is None:
        return None
    point = moment.text()
    text = f"{word} {point}"
    return PredicateHit(text, None, text, (word, point))


def _moment(node: object) -> _Moment | None:
    if not isinstance(node, dict):
        return None
    selector = node.get("NSSelectorName")
    if selector == "now":
        return _Moment("now", 0)
    if selector == "add:to:":
        base: _Moment | None = None
        offset = 0
        for argument in _array(node.get("NSArguments")):
            inner = _moment(argument)
            if inner is not None and inner.base != "clock":
                if base is not None:
                    return None
                base = inner
                continue
            minutes = _minutes(_constant(argument))
            if minutes is None:
                return None
            offset += minutes
        return base.shifted(offset) if base is not None else None
    keypath = _keypath(node)
    if keypath in _SUN:
        return _Moment(keypath, 0)
    minutes = _minutes(_constant(node))
    return _Moment("clock", minutes) if minutes is not None else None


def _minutes(value: object) -> int | None:
    """Hours and minutes of an archived NSDateComponents, in minutes."""
    if not isinstance(value, dict) or not {"NS.hour", "NS.minute"} & value.keys():
        return None
    total = 0
    for key, scale in (("NS.hour", 60), ("NS.minute", 1)):
        part = value.get(key)
        if isinstance(part, int) and not isinstance(part, bool) and abs(part) != _UNSET:
            total += part * scale
    return total


def _window(hits: list[PredicateHit]) -> PredicateHit | None:
    """after A with before B, joined by AND or by OR, is the window between A and B."""
    bounds = [hit.bound for hit in hits if hit.bound is not None]
    if len(hits) != 2 or len(bounds) != 2:
        return None
    sides = dict(bounds)
    if set(sides) != {"after", "before"}:
        return None
    text = f"between {sides['after']} and {sides['before']}"
    return PredicateHit(text, None, text)


def _joined(hits: list[PredicateHit], joiner: str) -> PredicateHit:
    pks = {hit.accessory_pk for hit in hits}
    accessory_pk = next(iter(pks)) if len(pks) == 1 else None
    return PredicateHit(joiner.join(hit.text for hit in hits), accessory_pk, joiner.join(hit.detail for hit in hits))


def _negated(hit: PredicateHit) -> PredicateHit:
    return PredicateHit(f"not ({hit.text})", hit.accessory_pk, f"not ({hit.detail})")


def _int(value: object, default: int) -> int:
    """An archived integer. 0 is a real value here, so it must not fall back to the default."""
    if not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _operator(node: object) -> int:
    if not isinstance(node, dict):
        return _EQUAL_TO
    return _int(node.get("NSOperatorType"), _EQUAL_TO)


def _relation(node: object) -> str:
    if not isinstance(node, dict):
        return "is"
    relation = _RELATIONS.get(_operator(node), "is")
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


def _array(node: object) -> list[object]:
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


def _root(blob: bytes | None) -> dict[str, object] | None:
    if not blob:
        return None
    try:
        loaded = plistlib.loads(blob)
    except (ValueError, TypeError, ExpatError):
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


def _resolve(objects: list[object], node: object, path: frozenset[int], depth: int) -> object:
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
