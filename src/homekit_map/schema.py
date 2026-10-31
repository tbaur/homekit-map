# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Read a HomeKit snapshot without writing it.

CoreData assigns Z_ENT numbers and junction-table names when it creates the
store. Those numbers differ across macOS versions, so they are read from the
file being queried.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from homekit_map.errors import SnapshotError
from homekit_map.paths import LIVE_HOMEKIT_DIR, is_within

ACTION_CLASS_LABELS = {
    "MKFCharacteristicWriteAction": "write",
    "MKFMatterCommandAction": "matter command",
    "MKFMediaPlaybackAction": "media playback",
    "MKFNaturalLightingAction": "adaptive lighting",
    "MKFShortcutAction": "shortcut",
}

EVENT_CLASS_LABELS = {
    "MKFCalendarEvent": "calendar",
    "MKFCharacteristicEvent": "characteristic",
    "MKFCharacteristicRangeEvent": "range",
    "MKFCharacteristicValueEvent": "value",
    "MKFDurationEvent": "duration",
    "MKFLocationEvent": "location",
    "MKFPresenceEvent": "presence",
    "MKFSignificantTimeEvent": "sun",
    "MKFMatterAttributeEvent": "matter attribute",
    "MKFMatterAttributeValueEvent": "matter value",
}

TRIGGER_CLASS_LABELS = {
    "MKFEventTrigger": "event",
    "MKFTimerTrigger": "timer",
}

_REQUIRED_CLASSES = (
    "MKFCharacteristicWriteAction",
    "MKFShortcutAction",
    "MKFEventTrigger",
    "MKFTimerTrigger",
    "MKFActionSet",
)


@dataclass(frozen=True)
class StoreSchema:
    """Ids and the trigger-to-action-set link for one snapshot."""

    classes: dict[str, int]
    action_labels: dict[int, str]
    event_labels: dict[int, str]
    trigger_labels: dict[int, str]
    junction: str
    action_column: str
    trigger_column: str

    def label(self, kind: str, entity_id: int | None) -> str:
        if entity_id is None:
            return "unknown"
        tables = {
            "action": self.action_labels,
            "event": self.event_labels,
            "trigger": self.trigger_labels,
        }
        return tables[kind].get(entity_id, f"ent_{entity_id}")


def quote_ident(name: str) -> str:
    """Quote a CoreData identifier. Reject names this store would not use."""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if not name.startswith("Z") or any(char not in allowed for char in name):
        raise SnapshotError(f"Unexpected SQLite identifier: {name}")
    return '"' + name + '"'


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Column names of a table, or an empty set when the table is missing."""
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quote_ident(table)})")}


def column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return column in table_columns(connection, table)


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def text_or_none(value: object) -> str | None:
    """Stripped text, or None when the value is missing or blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_active(value: object) -> bool:
    """HomeKit leaves ZACTIVE empty on some triggers. Only an explicit 0 is disabled."""
    return value is None or bool(value)


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open a snapshot read-only. Refuse the live HomeKit directory."""
    if path.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {path}")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise SnapshotError(
            f"No snapshot at {path}. From the repo, run: homekit-map backup"
        ) from exc
    live = LIVE_HOMEKIT_DIR.expanduser().resolve()
    if is_within(resolved, live):
        raise SnapshotError(
            "Refusing to open the live HomeKit database. "
            "Query the snapshot in data/core.sqlite."
        )
    connection = sqlite3.connect(_readonly_uri(resolved), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    query_only = connection.execute("PRAGMA query_only").fetchone()
    if query_only is None or query_only[0] != 1:
        connection.close()
        raise SnapshotError("SQLite did not keep this database read-only. Stopped.")
    return connection


def load_schema(connection: sqlite3.Connection) -> StoreSchema:
    """Read entity ids and the action-set junction from this snapshot."""
    try:
        rows = connection.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY").fetchall()
    except sqlite3.OperationalError as exc:
        raise SnapshotError("This file is not a HomeKit snapshot.") from exc
    classes = {str(row["Z_NAME"]): int(row["Z_ENT"]) for row in rows}
    missing = [name for name in _REQUIRED_CLASSES if name not in classes]
    if missing:
        raise SnapshotError(
            "This HomeKit snapshot is missing entity types: " + ", ".join(missing)
        )
    junction, action_column, trigger_column = _junction(connection)
    return StoreSchema(
        classes=classes,
        action_labels=_labels(classes, ACTION_CLASS_LABELS),
        event_labels=_labels(classes, EVENT_CLASS_LABELS),
        trigger_labels=_labels(classes, TRIGGER_CLASS_LABELS),
        junction=junction,
        action_column=action_column,
        trigger_column=trigger_column,
    )


def _labels(classes: dict[str, int], names: dict[str, str]) -> dict[int, str]:
    return {
        classes[class_name]: label
        for class_name, label in names.items()
        if class_name in classes
    }


def _junction(connection: sqlite3.Connection) -> tuple[str, str, str]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'Z_%TRIGGERS_'"
        )
    ]
    matches: list[tuple[str, str, str]] = []
    for name in tables:
        columns = [
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quote_ident(name)})")
        ]
        action_columns = [column for column in columns if "ACTIONSET" in column]
        trigger_columns = [
            column
            for column in columns
            if "TRIGGER" in column and "ACTIONSET" not in column
        ]
        if len(action_columns) == 1 and len(trigger_columns) == 1:
            matches.append((name, action_columns[0], trigger_columns[0]))
    if len(matches) != 1:
        found = ", ".join(tables) if tables else "none"
        raise SnapshotError(f"Could not find the trigger-to-action-set link. Tables: {found}")
    return matches[0]


def _readonly_uri(path: Path) -> str:
    quoted = quote(str(path), safe="/")
    return f"file://{quoted}?mode=ro"
