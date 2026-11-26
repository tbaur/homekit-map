# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Accessories, automations, and scenes that nothing runs, or that do nothing."""

from __future__ import annotations

import sqlite3

from homekit_map.model import Model
from homekit_map.query import Accessory, accessories_and_model
from homekit_map.schema import USER_SCENE


def format_unused(connection: sqlite3.Connection) -> str:
    """List what no automation or scene uses, and automations that cannot do anything."""
    accessories, model = accessories_and_model(connection)
    sections = (
        ("not in an automation or scene", _unused_accessories(accessories)),
        ("disabled automations", sorted((item.label for item in model.triggers.values() if not item.enabled), key=str.casefold)),
        ("scenes with no automation", _unlinked_scenes(model)),
        ("automations that do nothing", _idle_automations(model)),
    )
    lines: list[str] = []
    for heading, items in sections:
        lines.append(f"{heading}:")
        if items:
            lines.extend(f"  {item}" for item in items)
        else:
            lines.append("  none")
    return "\n".join(lines) + "\n"


def _unused_accessories(accessories: list[Accessory]) -> list[str]:
    """A bridge with no tiles of its own never takes part in an automation, so it is left off."""
    unused = [
        item
        for item in accessories
        if not (item.automations or item.scenes or item.triggers) and not (item.bridged and not item.services)
    ]
    lines: list[str] = []
    for item in sorted(unused, key=lambda entry: ((entry.room or "").casefold(), entry.display_name.casefold())):
        extra = []
        if item.room:
            extra.append(f"room={item.room}")
        device = " ".join(part for part in (item.manufacturer, item.model) if part)
        if device:
            extra.append(f"device={device}")
        tiles = [name for name in item.services if name != item.display_name]
        if tiles:
            extra.append(f"services={', '.join(tiles)}")
        suffix = f"  {'  '.join(extra)}" if extra else ""
        lines.append(f"{item.display_name}{suffix}")
    return lines


def _unlinked_scenes(model: Model) -> list[str]:
    """Scenes a person made that no automation runs. Built-in scenes are left off."""
    names = [
        item.name for item in model.sets.values() if item.kind == USER_SCENE and item.name and not item.triggers
    ]
    return sorted(names, key=str.casefold)


def _idle_automations(model: Model) -> list[str]:
    """No trigger means it never starts. No actions means it changes nothing when it does."""
    lines: list[tuple[str, str]] = []
    for trigger in model.triggers.values():
        reasons = []
        if not model.starts(trigger.pk):
            reasons.append("no trigger")
        if not model.actions_of(trigger.pk):
            reasons.append("no actions")
        if reasons:
            lines.append((trigger.label, ", ".join(reasons)))
    return [f"{label}  {reasons}" for label, reasons in sorted(lines, key=lambda item: item[0].casefold())]
