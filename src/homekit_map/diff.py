# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Compare two HomeKit snapshots."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from homekit_map.errors import SnapshotError
from homekit_map.paths import default_snapshot_path
from homekit_map.schema import open_readonly, table_columns, table_exists


@dataclass(frozen=True)
class Record:
    key: str
    label: str
    detail: str


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
    """Describe accessories, automations, and scenes that changed."""
    lines = [f"compared: {older_name} -> {newer_name}"]
    lines.extend(_section("accessories", _accessories(older), _accessories(newer)))
    lines.extend(_section("automations", _automations(older), _automations(newer)))
    lines.extend(_section("scenes", _scenes(older), _scenes(newer)))
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


def _section(title: str, older: dict[str, Record], newer: dict[str, Record]) -> list[str]:
    lines: list[str] = []
    for key in sorted(newer):
        if key not in older:
            lines.append(f"  added: {newer[key].label}")
    for key in sorted(older):
        if key not in newer:
            lines.append(f"  removed: {older[key].label}")
    for key in sorted(set(older) & set(newer)):
        before = older[key]
        after = newer[key]
        if before.label != after.label:
            lines.append(f"  renamed: {before.label} -> {after.label}")
        if before.detail == after.detail:
            continue
        if title == "accessories":
            lines.append(f"  moved: {after.label}  {before.detail or 'no room'} -> {after.detail or 'no room'}")
        else:
            lines.append(f"  changed: {after.label}  {before.detail} -> {after.detail}")
    if not lines:
        return []
    return [f"{title}:", *lines]


def _accessories(connection: sqlite3.Connection) -> dict[str, Record]:
    columns = table_columns(connection, "ZMKFACCESSORY")
    key = _key_column(columns, ("ZUNIQUEIDENTIFIER", "ZMODELID", "Z_PK"))
    room = (
        "(SELECT ZNAME FROM ZMKFROOM r WHERE r.Z_PK = a.ZROOM)"
        if table_exists(connection, "ZMKFROOM")
        else "NULL"
    )
    rows = connection.execute(
        f"""
        SELECT {key} AS key,
               COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '(unnamed)') AS label,
               {room} AS detail
        FROM ZMKFACCESSORY a
        """
    ).fetchall()
    return _records(rows)


def _automations(connection: sqlite3.Connection) -> dict[str, Record]:
    columns = table_columns(connection, "ZMKFTRIGGER")
    key = _key_column(columns, ("ZMODELID", "Z_PK"))
    rows = connection.execute(
        f"""
        SELECT {key} AS key,
               COALESCE(NULLIF(ZCONFIGUREDNAME, ''), ZNAME, '(unnamed)') AS label,
               CASE WHEN ZACTIVE = 0 THEN 'disabled' ELSE 'enabled' END AS detail
        FROM ZMKFTRIGGER
        """
    ).fetchall()
    return _records(rows)


def _scenes(connection: sqlite3.Connection) -> dict[str, Record]:
    columns = table_columns(connection, "ZMKFACTIONSET")
    key = _key_column(columns, ("ZMODELID", "Z_PK"))
    rows = connection.execute(
        f"""
        SELECT {key} AS key, ZNAME AS label, '' AS detail
        FROM ZMKFACTIONSET
        WHERE ZNAME IS NOT NULL AND ZNAME != ''
        """
    ).fetchall()
    return _records(rows)


def _records(rows: list[sqlite3.Row]) -> dict[str, Record]:
    found: dict[str, Record] = {}
    for row in rows:
        if row["key"] is None:
            continue
        key = str(row["key"])
        found[key] = Record(key, str(row["label"]), str(row["detail"] or ""))
    return found


def _key_column(columns: set[str], preferred: tuple[str, ...]) -> str:
    for name in preferred:
        if name in columns:
            return name
    raise SnapshotError("This snapshot has no id column to compare.")

