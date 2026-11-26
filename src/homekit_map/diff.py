# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Compare two HomeKit snapshots.

Objects are matched by HomeKit's model id, which a rename does not change.
ZUNIQUEIDENTIFIER cannot be the key: HomePods, Apple TVs, and AirPlay
speakers leave it empty. An edit inside an automation or scene is found by
comparing ids and stored values, so renaming an accessory does not make
every automation that uses it look edited.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from homekit_map.about import step_of, step_text
from homekit_map.errors import SnapshotError
from homekit_map.model import Model, load_model
from homekit_map.paths import default_snapshot_path
from homekit_map.schema import (
    HIDDEN_SERVICE_TYPES,
    TRIGGER_OWNED,
    open_readonly,
    table_columns,
    table_exists,
    text_or_none,
)

_TABLES = ("ZMKFACCESSORY", "ZMKFSERVICE", "ZMKFROOM", "ZMKFTRIGGER", "ZMKFACTIONSET")
_SERVICE_NAMES = ("ZNAME", "ZEXPECTEDCONFIGUREDNAME", "ZPROVIDEDNAME")

# One part of an automation or scene: a key made of ids and values, and the line that shows it.
_Part = tuple[tuple[object, ...], str]


@dataclass(frozen=True)
class _Contents:
    parts: tuple[_Part, ...]
    condition: bytes | None = None


@dataclass(frozen=True)
class Record:
    """One object in a snapshot. state is what a change compares, and shown is how it prints."""

    key: str
    label: str
    state: str = ""
    shown: str = ""
    owner: str = ""
    owner_key: str = ""


def format_versions(current: Path | None = None) -> str:
    """List saved snapshots, oldest first, with current last."""
    current = current or default_snapshot_path()
    lines = ["versions:"]
    saved = _saved(current)
    if not saved and not current.is_file():
        return "versions:\n  none\n"
    lines.extend(f"  {path.stem}" for path in saved)
    if current.is_file():
        lines.append("  current")
    return "\n".join(lines) + "\n"


def format_diff(
    older: sqlite3.Connection,
    newer: sqlite3.Connection,
    older_name: str = "previous",
    newer_name: str = "current",
) -> str:
    """Describe accessories, services, rooms, automations, and scenes that changed."""
    by_model = _model_tables(older, newer)
    before = _accessories(older, by_model)
    after = _accessories(newer, by_model)
    renamed = {key for key in before.keys() & after.keys() if before[key].label != after[key].label}
    old_model = _model(older)
    new_model = _model(newer)
    lines = [f"compared: {older_name} -> {newer_name}"]
    lines.extend(_section("accessories", before, after, "moved"))
    lines.extend(_renamed_services(_services(older, by_model), _services(newer, by_model), renamed))
    lines.extend(_section("rooms", _rooms(older, by_model), _rooms(newer, by_model)))
    lines.extend(
        _section(
            "automations",
            _automations(older, by_model),
            _automations(newer, by_model),
            edits=_edits(
                _automation_contents(old_model, "ZMKFTRIGGER" in by_model),
                _automation_contents(new_model, "ZMKFTRIGGER" in by_model),
            ),
        )
    )
    lines.extend(
        _section(
            "scenes",
            _scenes(older, by_model),
            _scenes(newer, by_model),
            edits=_edits(
                _scene_contents(old_model, "ZMKFACTIONSET" in by_model),
                _scene_contents(new_model, "ZMKFACTIONSET" in by_model),
            ),
        )
    )
    if len(lines) == 1:
        lines.append("no changes")
    return "\n".join(lines) + "\n"


def diff_current(current: Path | None = None, tokens: list[str] | None = None) -> str:
    """Compare the current snapshot with a saved one, or two saved ones."""
    current = current or default_snapshot_path()
    older_path, newer_path, older_name, newer_name = _pair(current, tokens or [])
    older = open_readonly(older_path)
    try:
        newer = open_readonly(newer_path)
        try:
            return format_diff(older, newer, older_name, newer_name)
        finally:
            newer.close()
    finally:
        older.close()


def _pair(current: Path, tokens: list[str]) -> tuple[Path, Path, str, str]:
    saved = _saved(current)
    if len(tokens) > 2:
        raise SnapshotError("diff takes the current snapshot, one version, or two versions.")
    if not tokens:
        if not current.is_file() or not saved:
            raise SnapshotError(
                "Need two snapshots to compare. The next backup keeps this copy."
            )
        older = saved[-1]
        return older, current, older.stem, "current"
    if len(tokens) == 1:
        if not current.is_file():
            raise SnapshotError(f"No current snapshot at {current.name}.")
        older = _resolve(current, tokens[0])
        return older, current, older.stem, "current"
    older = _resolve(current, tokens[0])
    newer = _resolve(current, tokens[1])
    return older, newer, older.stem, newer.stem


def _saved(current: Path) -> list[Path]:
    folder = current.parent / "versions"
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.glob("*.sqlite") if path.is_file())


def _resolve(current: Path, token: str) -> Path:
    direct = Path(token)
    if direct.is_file():
        return direct
    folder = current.parent / "versions"
    named = folder / f"{token}.sqlite"
    if named.is_file():
        return named
    matches = sorted(folder.glob(f"{token}*.sqlite")) if folder.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    raise SnapshotError(f"No snapshot version matches {token}.")


def _section(
    title: str,
    older: dict[str, Record],
    newer: dict[str, Record],
    change: str = "changed",
    edits: dict[str, list[str]] | None = None,
) -> list[str]:
    lines = [f"  added: {newer[key].label}" for key in _by_label(newer, newer.keys() - older.keys())]
    lines.extend(f"  removed: {older[key].label}" for key in _by_label(older, older.keys() - newer.keys()))
    for key in _by_label(newer, older.keys() & newer.keys()):
        before = older[key]
        after = newer[key]
        if before.label != after.label:
            lines.append(f"  renamed: {before.label} -> {after.label}")
        if before.state != after.state:
            lines.append(f"  {change}: {after.label}  {before.shown} -> {after.shown}")
        edited = (edits or {}).get(key)
        if edited:
            lines.append(f"  edited: {after.label}")
            lines.extend(edited)
    if not lines:
        return []
    return [f"{title}:", *lines]


def _model(connection: sqlite3.Connection) -> Model | None:
    """None when the snapshot lacks what automations need. Names are still compared."""
    try:
        return load_model(connection)
    except SnapshotError:
        return None


def _content_key(key: str, pk: int, by_model: bool) -> str:
    return key if by_model and not key.startswith("pk:") else f"pk:{pk}"


def _automation_contents(model: Model | None, by_model: bool) -> dict[str, _Contents]:
    if model is None:
        return {}
    found: dict[str, _Contents] = {}
    for trigger in model.triggers.values():
        parts: list[_Part] = [(("trigger", *event.key), f"trigger  {event.text}") for event in model.starts(trigger.pk)]
        parts.extend((("condition", hit.text), f"condition  {hit.text}") for hit in model.conditions.get(trigger.pk, ()))
        parts.extend((("turn off", *event.key), f"turn off  {event.text}") for event in model.ends(trigger.pk))
        for action_set in model.sets_of(trigger.pk):
            if action_set.is_scene:
                parts.append((("scene", action_set.key), f"scene  {action_set.name or '(unnamed)'}"))
                continue
            parts.extend(
                (("action", *action.key), f"action  {step_text(step_of(action))}")
                for action in model.by_set.get(action_set.pk, ())
            )
        found[_content_key(trigger.key, trigger.pk, by_model)] = _Contents(tuple(parts), trigger.condition)
    return found


def _scene_contents(model: Model | None, by_model: bool) -> dict[str, _Contents]:
    if model is None:
        return {}
    return {
        _content_key(action_set.key, action_set.pk, by_model): _Contents(
            tuple(
                (("action", *action.key), f"action  {step_text(step_of(action))}")
                for action in model.by_set.get(action_set.pk, ())
            )
        )
        for action_set in model.sets.values()
        if action_set.is_scene
    }


def _edits(older: dict[str, _Contents], newer: dict[str, _Contents]) -> dict[str, list[str]]:
    """Lines removed with -, then lines added with +. An unchanged condition is not compared by its text."""
    found: dict[str, list[str]] = {}
    for key in older.keys() & newer.keys():
        before = older[key].parts
        after = newer[key].parts
        if older[key].condition == newer[key].condition:
            before = tuple(part for part in before if part[0][0] != "condition")
            after = tuple(part for part in after if part[0][0] != "condition")
        removed = Counter(part[0] for part in before) - Counter(part[0] for part in after)
        added = Counter(part[0] for part in after) - Counter(part[0] for part in before)
        lines: list[str] = []
        for part_key, text in before:
            if removed[part_key] > 0:
                removed[part_key] -= 1
                lines.append(f"    - {text}")
        for part_key, text in after:
            if added[part_key] > 0:
                added[part_key] -= 1
                lines.append(f"    + {text}")
        if lines:
            found[key] = lines
    return found


def _renamed_services(older: dict[str, Record], newer: dict[str, Record], renamed: set[str]) -> list[str]:
    """Tiles whose name changed. A tile that took its accessory's new name is already reported."""
    lines: list[str] = []
    for key in _by_label(newer, older.keys() & newer.keys()):
        before = older[key]
        after = newer[key]
        if before.label == after.label:
            continue
        if after.owner_key in renamed and after.label == after.owner:
            continue
        lines.append(f"  renamed: {before.label} -> {after.label}  accessory={after.owner}")
    if not lines:
        return []
    return ["services:", *lines]


def _by_label(records: dict[str, Record], keys: set[str]) -> list[str]:
    return sorted(keys, key=lambda key: (records[key].label.casefold(), key))


def _model_tables(older: sqlite3.Connection, newer: sqlite3.Connection) -> frozenset[str]:
    """Tables where both snapshots have a model id. Other tables fall back to the row id."""
    return frozenset(
        table
        for table in _TABLES
        if "ZMODELID" in table_columns(older, table) and "ZMODELID" in table_columns(newer, table)
    )


def _model_id(connection: sqlite3.Connection, table: str, alias: str) -> str:
    return f"{alias}.ZMODELID" if "ZMODELID" in table_columns(connection, table) else "NULL"


def _key(model: object, pk: object, by_model: bool) -> str:
    if by_model and isinstance(model, bytes) and model:
        return model.hex()
    return f"pk:{pk}"


def _accessories(connection: sqlite3.Connection, by_model: frozenset[str]) -> dict[str, Record]:
    if table_exists(connection, "ZMKFROOM"):
        room = f"r.Z_PK AS room_pk, {_model_id(connection, 'ZMKFROOM', 'r')} AS room_model, r.ZNAME AS room"
        join = "LEFT JOIN ZMKFROOM r ON r.Z_PK = a.ZROOM"
    else:
        room = "NULL AS room_pk, NULL AS room_model, NULL AS room"
        join = ""
    rows = connection.execute(
        f"""
        SELECT a.Z_PK AS pk, {_model_id(connection, 'ZMKFACCESSORY', 'a')} AS model,
               COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '(unnamed)') AS label,
               {room}
        FROM ZMKFACCESSORY a
        {join}
        """
    ).fetchall()
    found: dict[str, Record] = {}
    for row in rows:
        key = _key(row["model"], row["pk"], "ZMKFACCESSORY" in by_model)
        room_key = "" if row["room_pk"] is None else _key(row["room_model"], row["room_pk"], "ZMKFROOM" in by_model)
        found[key] = Record(key, str(row["label"]), room_key, text_or_none(row["room"]) or "no room")
    return found


def _services(connection: sqlite3.Connection, by_model: frozenset[str]) -> dict[str, Record]:
    columns = table_columns(connection, "ZMKFSERVICE")
    names = [f"NULLIF(s.{column}, '')" for column in _SERVICE_NAMES if column in columns]
    if not names or "ZACCESSORY" not in columns:
        return {}
    kind = "s.ZSERVICETYPE" if "ZSERVICETYPE" in columns else "NULL"
    rows = connection.execute(
        f"""
        SELECT s.Z_PK AS pk, {_model_id(connection, 'ZMKFSERVICE', 's')} AS model,
               COALESCE({", ".join(names)}, '(unnamed)') AS label, {kind} AS kind,
               a.Z_PK AS owner_pk, {_model_id(connection, 'ZMKFACCESSORY', 'a')} AS owner_model,
               COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '(unnamed)') AS owner
        FROM ZMKFSERVICE s
        JOIN ZMKFACCESSORY a ON a.Z_PK = s.ZACCESSORY
        """
    ).fetchall()
    found: dict[str, Record] = {}
    for row in rows:
        if row["kind"] in HIDDEN_SERVICE_TYPES:
            continue
        key = _key(row["model"], row["pk"], "ZMKFSERVICE" in by_model)
        owner_key = _key(row["owner_model"], row["owner_pk"], "ZMKFACCESSORY" in by_model)
        found[key] = Record(key, str(row["label"]), owner=str(row["owner"]), owner_key=owner_key)
    return found


def _rooms(connection: sqlite3.Connection, by_model: frozenset[str]) -> dict[str, Record]:
    if not table_exists(connection, "ZMKFROOM"):
        return {}
    rows = connection.execute(
        f"""
        SELECT r.Z_PK AS pk, {_model_id(connection, 'ZMKFROOM', 'r')} AS model,
               COALESCE(NULLIF(r.ZNAME, ''), '(unnamed)') AS label, '' AS state
        FROM ZMKFROOM r
        """
    ).fetchall()
    return _records(rows, "ZMKFROOM" in by_model)


def _automations(connection: sqlite3.Connection, by_model: frozenset[str]) -> dict[str, Record]:
    rows = connection.execute(
        f"""
        SELECT t.Z_PK AS pk, {_model_id(connection, 'ZMKFTRIGGER', 't')} AS model,
               COALESCE(NULLIF(t.ZCONFIGUREDNAME, ''), t.ZNAME, '(unnamed)') AS label,
               CASE WHEN t.ZACTIVE = 0 THEN 'disabled' ELSE 'enabled' END AS state
        FROM ZMKFTRIGGER t
        """
    ).fetchall()
    return _records(rows, "ZMKFTRIGGER" in by_model)


def _scenes(connection: sqlite3.Connection, by_model: frozenset[str]) -> dict[str, Record]:
    """Scenes the Home app shows. Automations own hidden action sets, often named with a UUID."""
    rows = connection.execute(
        f"""
        SELECT aset.Z_PK AS pk, {_model_id(connection, 'ZMKFACTIONSET', 'aset')} AS model,
               aset.ZNAME AS label, '' AS state
        FROM ZMKFACTIONSET aset
        WHERE aset.ZNAME IS NOT NULL AND aset.ZNAME != ''
          AND COALESCE(aset.ZTYPE, '') != ?
        """,
        (TRIGGER_OWNED,),
    ).fetchall()
    return _records(rows, "ZMKFACTIONSET" in by_model)


def _records(rows: list[sqlite3.Row], by_model: bool) -> dict[str, Record]:
    found: dict[str, Record] = {}
    for row in rows:
        key = _key(row["model"], row["pk"], by_model)
        state = str(row["state"] or "")
        found[key] = Record(key, str(row["label"]), state, state)
    return found
