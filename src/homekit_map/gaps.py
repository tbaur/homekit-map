# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Accessories, automations, and scenes that nothing runs."""

from __future__ import annotations

import sqlite3

from homekit_map.schema import load_schema, quote_ident


def format_unused(connection: sqlite3.Connection) -> str:
    """List what is not part of an automation."""
    schema = load_schema(connection)
    lines = ["not in an automation:"]
    accessories = _unused_accessories(connection)
    if accessories:
        lines.extend(f"  {line}" for line in accessories)
    else:
        lines.append("  none")
    lines.append("disabled automations:")
    disabled = _disabled(connection)
    lines.extend(f"  {name}" for name in disabled) if disabled else lines.append("  none")
    lines.append("scenes with no automation:")
    scenes = _unlinked_scenes(connection, schema)
    lines.extend(f"  {name}" for name in scenes) if scenes else lines.append("  none")
    return "\n".join(lines) + "\n"


def _unused_accessories(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '(unnamed)') AS label,
               r.ZNAME AS room,
               a.ZMANUFACTURER AS manufacturer,
               a.ZMODEL AS model
        FROM ZMKFACCESSORY a
        LEFT JOIN ZMKFROOM r ON r.Z_PK = a.ZROOM
        WHERE NOT EXISTS (
            SELECT 1 FROM ZMKFACTION act
            WHERE act.ZACCESSORY1 = a.Z_PK
               OR act.ZACCESSORY2 = a.Z_PK
               OR act.ZACCESSORY = a.Z_PK
        )
        AND NOT EXISTS (
            SELECT 1
            FROM ZMKFSERVICE s
            JOIN ZMKFEVENT e ON e.ZSERVICE = s.Z_PK
            WHERE s.ZACCESSORY = a.Z_PK
        )
        ORDER BY r.ZNAME, label
        """
    ).fetchall()
    lines = []
    for row in rows:
        extra = []
        if row["room"]:
            extra.append(f"room={row['room']}")
        device = " ".join(part for part in (row["manufacturer"], row["model"]) if part)
        if device:
            extra.append(f"device={device}")
        suffix = f"  {'  '.join(extra)}" if extra else ""
        lines.append(f"{row['label']}{suffix}")
    return lines


def _disabled(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT COALESCE(NULLIF(ZCONFIGUREDNAME, ''), ZNAME, '(unnamed)') AS label
        FROM ZMKFTRIGGER
        WHERE ZACTIVE = 0
        ORDER BY label
        """
    ).fetchall()
    return [str(row["label"]) for row in rows]


def _unlinked_scenes(connection: sqlite3.Connection, schema) -> list[str]:
    junction = quote_ident(schema.junction)
    action_column = quote_ident(schema.action_column)
    rows = connection.execute(
        f"""
        SELECT aset.ZNAME AS label
        FROM ZMKFACTIONSET aset
        LEFT JOIN {junction} jt ON jt.{action_column} = aset.Z_PK
        WHERE aset.ZTYPE = 'HMActionSetTypeUserDefined'
          AND jt.{action_column} IS NULL
          AND aset.ZNAME IS NOT NULL
          AND aset.ZNAME != ''
        ORDER BY label
        """
    ).fetchall()
    return [str(row["label"]) for row in rows]
