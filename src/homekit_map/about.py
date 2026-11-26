# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Look up a name wherever it appears in the snapshot.

A name can be an accessory, a service, an automation, a scene, or a room.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from homekit_map.model import Action, ActionSet, Model, load_model
from homekit_map.names import Names, load_names
from homekit_map.query import Accessory, find_accessories, format_accessories
from homekit_map.schema import text_or_none


@dataclass(frozen=True)
class Step:
    """One write inside an automation or scene. scene names the scene an automation runs it through."""

    accessory: str | None
    service: str | None
    characteristic: str | None
    target: str | None
    kind: str
    scene: str | None = None


@dataclass(frozen=True)
class Automation:
    name: str
    enabled: bool
    trigger_type: str
    starts: tuple[str, ...]
    actions: tuple[Step, ...]
    conditions: tuple[str, ...] = ()
    ends: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scene:
    name: str
    actions: tuple[Step, ...]
    used_by: tuple[str, ...]


@dataclass(frozen=True)
class Room:
    """A room and its accessories, each with the tiles that have a name of their own."""

    name: str
    accessories: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Report:
    accessories: tuple[Accessory, ...]
    automations: tuple[Automation, ...]
    scenes: tuple[Scene, ...]
    rooms: tuple[Room, ...]
    kind: str | None = None


_KINDS = {
    "room": "room",
    "rooms": "room",
    "accessory": "accessory",
    "accessories": "accessory",
    "automation": "automation",
    "automations": "automation",
    "scene": "scene",
    "scenes": "scene",
    "maker": "maker",
    "makers": "maker",
    "day": "day",
    "days": "day",
    "unused": "unused",
}


def split_search(text: str) -> tuple[str | None, str]:
    """A leading kind word limits the lookup. The rest is the name."""
    parts = text.split()
    if parts and parts[0].casefold() in {"day", "days", "unused"}:
        return _KINDS[parts[0].casefold()], " ".join(parts[1:])
    if len(parts) >= 2 and parts[0].casefold() in _KINDS:
        return _KINDS[parts[0].casefold()], " ".join(parts[1:])
    return None, text.strip()


def describe(connection: sqlite3.Connection, text: str) -> Report:
    """Return every object whose name contains text, with what the snapshot knows."""
    kind, needle = split_search(text)
    needle_cf = needle.casefold()
    if not needle_cf:
        return Report((), (), (), (), kind)
    accessories: tuple[Accessory, ...] = ()
    automations: tuple[Automation, ...] = ()
    scenes: tuple[Scene, ...] = ()
    rooms: tuple[Room, ...] = ()
    names = load_names(connection)
    if kind in (None, "accessory", "maker"):
        accessories = tuple(find_accessories(connection, needle, kind))
    if kind in (None, "automation", "scene"):
        model = load_model(connection, names)
        if kind in (None, "automation"):
            automations = tuple(_matching_automations(model, needle_cf))
        if kind in (None, "scene"):
            scenes = tuple(_matching_scenes(model, needle_cf))
    if kind in (None, "room"):
        rooms = tuple(_matching_rooms(connection, names, needle_cf))
    return Report(accessories, automations, scenes, rooms, kind)


def format_report(report: Report) -> str:
    """Plain text covering every kind of match."""
    if report.kind:
        return _format_one_kind(report)
    lines = [
        f"accessories: {len(report.accessories)}",
        f"automations: {len(report.automations)}",
        f"scenes: {len(report.scenes)}",
        f"rooms: {len(report.rooms)}",
    ]
    if report.accessories:
        lines.append("")
        lines.append(format_accessories(list(report.accessories)).rstrip("\n"))
    for automation in report.automations:
        lines.extend(_automation_lines(automation))
    for scene in report.scenes:
        lines.extend(_scene_lines(scene))
    for room in report.rooms:
        lines.append("")
        lines.extend(_room_lines(room))
    return "\n".join(lines) + "\n"


def step_of(action: Action, scene: str | None = None) -> Step:
    return Step(
        accessory=action.accessory,
        service=action.service,
        characteristic=action.characteristic,
        target=action.target,
        kind=action.role,
        scene=scene,
    )


def step_text(step: Step) -> str:
    lead = [f"scene {step.scene}"] if step.scene else []
    if step.kind == "adaptive lighting":
        who = step.accessory or "light"
        state = "on" if step.target == "on" else "off"
        return "  ".join([*lead, who, f"adaptive lighting {state}"])
    if step.accessory:
        detail = [*lead, step.accessory]
        if step.kind not in {"write", "action"}:
            detail.append(step.kind)
    else:
        detail = [*lead, step.kind]
    if step.service:
        detail.append(f"service={step.service}")
    if step.characteristic:
        detail.append(f"characteristic={step.characteristic}")
    if step.target:
        detail.append(f"target={step.target}")
    return "  ".join(detail)


def automation_steps(model: Model, trigger_pk: int) -> tuple[Step, ...]:
    """The automation's own actions first, then each scene it runs."""
    steps: list[Step] = []
    for action_set in _ordered_sets(model.sets_of(trigger_pk)):
        scene = (action_set.name or "(unnamed)") if action_set.is_scene else None
        steps.extend(step_of(action, scene) for action in model.by_set.get(action_set.pk, ()))
    return tuple(steps)


def _ordered_sets(sets: tuple[ActionSet, ...]) -> list[ActionSet]:
    return sorted(sets, key=lambda item: (item.is_scene, (item.name or "").casefold(), item.pk))


def _format_one_kind(report: Report) -> str:
    """Show only the kind the search asked for."""
    if report.kind == "room":
        if not report.rooms:
            return "rooms: 0\n"
        lines: list[str] = []
        for item in report.rooms:
            lines.extend(_room_lines(item))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    if report.kind == "automation":
        if not report.automations:
            return "automations: 0\n"
        lines = []
        for automation in report.automations:
            lines.extend(_automation_lines(automation))
        return "\n".join(lines).lstrip("\n") + "\n"
    if report.kind == "scene":
        if not report.scenes:
            return "scenes: 0\n"
        lines = []
        for scene in report.scenes:
            lines.extend(_scene_lines(scene))
        return "\n".join(lines).lstrip("\n") + "\n"
    if not report.accessories:
        label = "makers" if report.kind == "maker" else "accessories"
        return f"{label}: 0\n"
    return format_accessories(list(report.accessories))


def _automation_lines(item: Automation) -> list[str]:
    state = "enabled" if item.enabled else "disabled"
    lines = ["", f"automation: {item.name}", f"  {state}", f"  type: {item.trigger_type}"]
    _append_texts(lines, "trigger", item.starts, always=True)
    _append_texts(lines, "condition", item.conditions)
    _append_texts(lines, "turn off", item.ends)
    _append_steps(lines, "actions", item.actions)
    return lines


def _append_texts(lines: list[str], heading: str, texts: tuple[str, ...], *, always: bool = False) -> None:
    if texts:
        lines.append(f"  {heading}:")
        lines.extend(f"    {text}" for text in texts)
    elif always:
        lines.append(f"  {heading}: none")


def _scene_lines(item: Scene) -> list[str]:
    lines = ["", f"scene: {item.name}"]
    _append_steps(lines, "actions", item.actions)
    if item.used_by:
        lines.append("  used by:")
        lines.extend(f"    {name}" for name in item.used_by)
    else:
        lines.append("  used by: none")
    return lines


def _room_lines(item: Room) -> list[str]:
    lines = [f"room: {item.name}"]
    if not item.accessories:
        lines.append("  accessories: none")
        return lines
    lines.append("  accessories:")
    for name, tiles in item.accessories:
        lines.append(f"    {name}")
        lines.extend(f"      {tile}" for tile in tiles)
    return lines


def _append_steps(lines: list[str], heading: str, steps: tuple[Step, ...]) -> None:
    if not steps:
        lines.append(f"  {heading}: none")
        return
    lines.append(f"  {heading}:")
    lines.extend(f"    {step_text(step)}" for step in steps)


def _matching_automations(model: Model, needle: str) -> list[Automation]:
    found: list[Automation] = []
    for trigger in model.triggers.values():
        if needle not in trigger.label.casefold():
            continue
        found.append(
            Automation(
                name=trigger.label,
                enabled=trigger.enabled,
                trigger_type=trigger.kind,
                starts=tuple(dict.fromkeys(event.text for event in model.starts(trigger.pk))),
                actions=automation_steps(model, trigger.pk),
                conditions=tuple(hit.text for hit in model.conditions.get(trigger.pk, ())),
                ends=tuple(dict.fromkeys(event.text for event in model.ends(trigger.pk))),
            )
        )
    return sorted(found, key=lambda item: item.name.casefold())


def _matching_scenes(model: Model, needle: str) -> list[Scene]:
    found: list[Scene] = []
    for action_set in model.sets.values():
        if not action_set.is_scene or not action_set.name or needle not in action_set.name.casefold():
            continue
        used_by = sorted(
            (model.triggers[pk].label for pk in action_set.triggers if pk in model.triggers),
            key=str.casefold,
        )
        found.append(
            Scene(
                name=action_set.name,
                actions=tuple(step_of(action) for action in model.by_set.get(action_set.pk, ())),
                used_by=tuple(used_by),
            )
        )
    return sorted(found, key=lambda item: item.name.casefold())


def _matching_rooms(connection: sqlite3.Connection, names: Names, needle: str) -> list[Room]:
    found: list[Room] = []
    rows = connection.execute("SELECT Z_PK, ZNAME FROM ZMKFROOM").fetchall()
    for row in rows:
        name = text_or_none(row["ZNAME"])
        if name is None or needle not in name.casefold():
            continue
        members: list[tuple[str, tuple[str, ...]]] = []
        for (pk,) in connection.execute("SELECT Z_PK FROM ZMKFACCESSORY WHERE ZROOM = ?", (int(row["Z_PK"]),)):
            label = names.accessory(int(pk)) or "(unnamed)"
            tiles = tuple(tile for tile in names.tile_names(int(pk)) if tile != label)
            members.append((label, tiles))
        found.append(Room(name=name, accessories=tuple(sorted(members, key=lambda item: item[0].casefold()))))
    return found
