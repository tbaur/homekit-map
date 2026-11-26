# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Answer which automations and scenes use an accessory.

Reads the snapshot only. A renamed accessory matches either its current
HomeKit name, the name it was given originally, or the name of any tile.
"""

from __future__ import annotations

import plistlib
import sqlite3
from collections import Counter
from dataclasses import dataclass, replace
from xml.parsers.expat import ExpatError

from homekit_map.events import Event
from homekit_map.model import Action, Model, load_model
from homekit_map.names import Names, load_names
from homekit_map.schema import TRIGGER_CLASS_LABELS, column_or_null, quote_ident, table_columns, text_or_none

_CAMERA_BULLETINS = {
    "MKFCameraAccessModeBulletinRegistration": "camera access mode",
    "MKFCameraReachabilityBulletinRegistration": "camera reachability",
    "MKFCameraSignificantEventBulletinRegistration": "camera activity",
}
# CoreData gives each kind of registration its own accessory column, numbered per store.
_BULLETIN_ACCESSORY_COLUMNS = ("ZACCESSORY", "ZACCESSORY1", "ZACCESSORY2", "ZACCESSORY3")
# On an accessory, more than this many triggers that read the same become one line with a count.
_GROUPED_TRIGGERS = 3
_TRIGGER_ROLES = frozenset(TRIGGER_CLASS_LABELS.values())
_ROLE_ORDER = {
    "trigger": 0,
    "condition": 1,
    "action": 3,
    "adaptive lighting": 4,
    "media playback": 5,
    "shortcut": 6,
    "turn off": 7,
}


@dataclass(frozen=True)
class Use:
    """One place an accessory is written or watched.

    automation_pk tells apart automations that share a name. scene names the
    scene an automation runs when the write comes from that scene.
    """

    role: str
    name: str
    enabled: bool | None
    service: str | None
    characteristic: str | None
    target: str | None
    also: tuple[str, ...] = ()
    automation_pk: int | None = None
    scene: str | None = None


@dataclass(frozen=True)
class Accessory:
    """An accessory and the automations, scenes, and triggers that name it."""

    pk: int
    configured_name: str | None
    provided_name: str | None
    manufacturer: str | None
    model: str | None
    room: str | None
    services: tuple[str, ...]
    automations: tuple[Use, ...]
    scenes: tuple[Use, ...]
    triggers: tuple[Use, ...]
    notifications: tuple[str, ...] = ()
    conditions: tuple[Use, ...] = ()
    bridged: int = 0

    @property
    def display_name(self) -> str:
        return self.configured_name or self.provided_name or "(unnamed)"

    def match_names(self) -> tuple[str, ...]:
        values = [
            self.configured_name,
            self.provided_name,
            self.manufacturer,
            self.model,
            *self.services,
        ]
        return tuple(value for value in values if value)

    def own_names(self) -> tuple[str, ...]:
        """Names of the accessory and its tiles, without the maker or model."""
        values = [self.configured_name, self.provided_name, *self.services]
        return tuple(value for value in values if value)


@dataclass(frozen=True)
class _Shortcut:
    text: str
    set_pk: int


@dataclass(frozen=True)
class _Context:
    """What every accessory lookup needs, read once per command."""

    model: Model
    known_names: tuple[str, ...]
    shortcuts: tuple[_Shortcut, ...]
    writers: dict[int, tuple[tuple[int, str], ...]]
    by_accessory: dict[int, tuple[Action, ...]]
    bulletin_columns: frozenset[str]
    camera_columns: dict[int, str]


def find_accessories(
    connection: sqlite3.Connection, text: str, scope: str | None = None
) -> list[Accessory]:
    """Return accessories whose names contain text, with their uses filled in."""
    needle = text.casefold().strip()
    if not needle:
        return []
    names = load_names(connection)
    matched = [
        item for item in _load_accessories(connection, names) if _matches(item, needle, scope)
    ]
    if not matched:
        return []
    context = _load_context(connection, names)
    return [_with_uses(connection, context, item) for item in matched]


def all_accessories(connection: sqlite3.Connection) -> list[Accessory]:
    """Return every accessory, including ones no automation uses."""
    return accessories_and_model(connection)[0]


def accessories_and_model(connection: sqlite3.Connection) -> tuple[list[Accessory], Model]:
    """Every accessory with its uses, and the model they were read from."""
    names = load_names(connection)
    context = _load_context(connection, names)
    return [_with_uses(connection, context, item) for item in _load_accessories(connection, names)], context.model


def format_accessories(items: list[Accessory]) -> str:
    """Plain text for a terminal. Stable enough to compare."""
    lines = [f"accessories: {len(items)}"]
    for index, item in enumerate(items, start=1):
        lines.append("")
        lines.append(f"accessory {index}: {item.display_name}")
        if item.configured_name and item.configured_name != item.display_name:
            lines.append(f"  configured: {item.configured_name}")
        if item.provided_name and item.provided_name != item.display_name:
            lines.append(f"  provided: {item.provided_name}")
        if item.room:
            lines.append(f"  room: {item.room}")
        device = " ".join(part for part in (item.manufacturer, item.model) if part)
        if device:
            lines.append(f"  device: {device}")
        if item.bridged:
            lines.append(f"  bridge: {item.bridged} {'accessory' if item.bridged == 1 else 'accessories'}")
        tiles = [name for name in item.services if name != item.display_name]
        if tiles:
            lines.append("  services:")
            lines.extend(f"    {name}" for name in tiles)
        _append_uses(lines, "automations", item.automations)
        _append_uses(lines, "scenes", item.scenes)
        _append_uses(lines, "triggers", item.triggers)
        if item.notifications:
            lines.append("  notifications:")
            lines.extend(f"    {note}" for note in item.notifications)
        else:
            lines.append("  notifications: none")
    return "\n".join(lines) + "\n"


def trigger_texts(starts: tuple[Event, ...]) -> tuple[str, ...]:
    """What starts an automation. Many accessories that test the same thing share one line."""
    counts = Counter(event.detail for event in starts if event.accessory_pk is not None)
    texts: list[str] = []
    for event in starts:
        count = counts[event.detail] if event.accessory_pk is not None else 0
        text = f"{event.detail}  ({count} accessories)" if count > _GROUPED_TRIGGERS else event.text
        if text not in texts:
            texts.append(text)
    return tuple(texts)


def _matches(item: Accessory, needle: str, scope: str | None = None) -> bool:
    fields: tuple[str | None, ...]
    if scope == "maker":
        fields = (item.manufacturer, item.model)
    elif scope == "accessory":
        fields = item.own_names()
    else:
        fields = item.match_names()
    return any(needle in name.casefold() for name in fields if name)


def _append_uses(lines: list[str], heading: str, uses: tuple[Use, ...]) -> None:
    if not uses:
        lines.append(f"  {heading}: none")
        return
    lines.append(f"  {heading}:")
    grouped: dict[tuple[str, bool | None, int | None], list[Use]] = {}
    for use in uses:
        grouped.setdefault((use.name, use.enabled, use.automation_pk), []).append(use)
    for group in grouped.values():
        lines.append(f"    {_use_heading(group[0])}")
        for use in sorted(group, key=_use_order):
            lines.append(f"      {_use_detail(use)}")
        also = list(dict.fromkeys(name for use in group for name in use.also))
        if also:
            lines.append("      also:")
            lines.extend(f"        {name}" for name in also)


def _use_heading(use: Use) -> str:
    if use.enabled is True:
        return f"{use.name} [enabled]"
    if use.enabled is False:
        return f"{use.name} [disabled]"
    return use.name


def _use_order(use: Use) -> int:
    """Eve's order: what starts it, the test, then the scene or accessories, then when it turns off."""
    if use.scene and use.role not in ("trigger", "condition", "turn off"):
        return 2
    return _ROLE_ORDER.get(use.role, 6)


def _use_detail(use: Use) -> str:
    if use.role in ("trigger", "condition", "turn off"):
        return f"{use.role}  {use.target}" if use.target else use.role
    if use.role in _TRIGGER_ROLES:
        text = use.target or use.role
        return f"{text}  service={use.service}" if use.service else text
    lead = [f"scene {use.scene}"] if use.scene else []
    if use.role == "adaptive lighting":
        return "  ".join([*lead, f"adaptive lighting {use.target or 'off'}"])
    parts = [*lead, use.role] if not lead or use.role != "action" else lead
    if use.service:
        parts.append(f"service={use.service}")
    if use.characteristic:
        parts.append(f"characteristic={use.characteristic}")
    if use.target:
        parts.append(f"target={use.target}")
    return "  ".join(parts)


def _load_accessories(connection: sqlite3.Connection, names: Names) -> list[Accessory]:
    rows = connection.execute(
        """
        SELECT a.Z_PK, a.ZCONFIGUREDNAME, a.ZPROVIDEDNAME, a.ZMANUFACTURER, a.ZMODEL,
               r.ZNAME AS room
        FROM ZMKFACCESSORY a
        LEFT JOIN ZMKFROOM r ON r.Z_PK = a.ZROOM
        ORDER BY COALESCE(NULLIF(a.ZCONFIGUREDNAME, ''), a.ZPROVIDEDNAME, '')
        """
    ).fetchall()
    return [
        Accessory(
            pk=int(row["Z_PK"]),
            configured_name=text_or_none(row["ZCONFIGUREDNAME"]),
            provided_name=text_or_none(row["ZPROVIDEDNAME"]),
            manufacturer=text_or_none(row["ZMANUFACTURER"]),
            model=text_or_none(row["ZMODEL"]),
            room=text_or_none(row["room"]),
            services=names.tile_names(int(row["Z_PK"])),
            automations=(),
            scenes=(),
            triggers=(),
            bridged=names.hosted.get(int(row["Z_PK"]), 0),
        )
        for row in rows
    ]


def _load_context(connection: sqlite3.Connection, names: Names) -> _Context:
    model = load_model(connection, names)
    writers: dict[int, list[tuple[int, str]]] = {}
    by_accessory: dict[int, list[Action]] = {}
    shortcuts: list[_Shortcut] = []
    for action in model.actions:
        if action.accessory_pk is not None:
            by_accessory.setdefault(action.accessory_pk, []).append(action)
        if action.role == "shortcut" and action.data:
            shortcuts.append(_Shortcut(_shortcut_text(action.data), action.set_pk))
        action_set = model.sets.get(action.set_pk)
        if action_set is None or action.accessory_pk is None or not action.tile:
            continue
        for trigger_pk in action_set.triggers:
            writers.setdefault(trigger_pk, []).append((action.accessory_pk, action.tile))
    bulletins = frozenset(table_columns(connection, "ZMKFBULLETINREGISTRATION"))
    return _Context(
        model=model,
        known_names=_known_names(connection),
        shortcuts=tuple(shortcuts),
        writers={pk: tuple(items) for pk, items in writers.items()},
        by_accessory={pk: tuple(items) for pk, items in by_accessory.items()},
        bulletin_columns=bulletins,
        camera_columns=_camera_columns(connection, model, bulletins),
    )


def _with_uses(connection: sqlite3.Connection, context: _Context, item: Accessory) -> Accessory:
    automations, scenes = _action_uses(context, item.pk)
    shortcut_automations, shortcut_scenes = _shortcut_mentions(context, item)
    automations = _dedupe(automations + shortcut_automations + _condition_uses(context, item.pk))
    return replace(
        item,
        automations=_with_automation_lines(context, automations, item.pk),
        scenes=_dedupe(scenes + shortcut_scenes),
        triggers=_trigger_uses(context, item),
        notifications=_notifications(connection, context, item.pk),
    )


def _companions(context: _Context, trigger_pk: int | None, accessory_pk: int) -> tuple[str, ...]:
    """Other accessories the same automation writes, by the name of the tile it writes."""
    if trigger_pk is None:
        return ()
    labels = {label for pk, label in context.writers.get(trigger_pk, ()) if pk != accessory_pk}
    return tuple(sorted(labels))


def _placed(context: _Context, set_pk: int, use: Use, accessory_pk: int | None) -> tuple[list[Use], list[Use]]:
    """Put one use on its scene, and on every automation that runs its action set."""
    model = context.model
    action_set = model.sets.get(set_pk)
    automations: list[Use] = []
    scenes: list[Use] = []
    if action_set is None:
        return automations, scenes
    scene = (action_set.name or "(unnamed)") if action_set.is_scene else None
    if scene is not None:
        scenes.append(replace(use, name=scene))
    for trigger_pk in action_set.triggers:
        trigger = model.triggers.get(trigger_pk)
        if trigger is None:
            continue
        automations.append(
            replace(
                use,
                name=trigger.label,
                enabled=trigger.enabled,
                also=_companions(context, trigger_pk, accessory_pk) if accessory_pk is not None else (),
                automation_pk=trigger_pk,
                scene=scene,
            )
        )
    return automations, scenes


def _action_uses(context: _Context, accessory_pk: int) -> tuple[tuple[Use, ...], tuple[Use, ...]]:
    automations: list[Use] = []
    scenes: list[Use] = []
    for action in context.by_accessory.get(accessory_pk, ()):
        placed_automations, placed_scenes = _placed(context, action.set_pk, _write_use(action), accessory_pk)
        automations.extend(placed_automations)
        scenes.extend(placed_scenes)
    return _dedupe(tuple(automations)), _dedupe(tuple(scenes))


def _write_use(action: Action) -> Use:
    """The write itself, before it is placed on an automation or a scene."""
    if action.role == "adaptive lighting":
        return Use(role=action.role, name="", enabled=None, service=None, characteristic=None, target=action.target)
    return Use(
        role=action.role,
        name="",
        enabled=None,
        service=action.service,
        characteristic=action.characteristic,
        target=action.target,
    )


def _condition_uses(context: _Context, accessory_pk: int) -> tuple[Use, ...]:
    model = context.model
    found: list[Use] = []
    for trigger_pk, hits in model.conditions.items():
        trigger = model.triggers.get(trigger_pk)
        if trigger is None:
            continue
        found.extend(
            Use(
                role="condition",
                name=trigger.label,
                enabled=trigger.enabled,
                service=None,
                characteristic=None,
                target=hit.detail,
                also=_companions(context, trigger_pk, accessory_pk),
                automation_pk=trigger_pk,
            )
            for hit in hits
            if hit.accessory_pk == accessory_pk
        )
    return tuple(found)


def _with_automation_lines(context: _Context, uses: tuple[Use, ...], accessory_pk: int) -> tuple[Use, ...]:
    """Put each automation's triggers, other conditions, and Turn Off setting on that automation."""
    model = context.model
    seen: set[int] = set()
    extra: list[Use] = []
    for use in uses:
        pk = use.automation_pk
        if pk is None or pk in seen:
            continue
        seen.add(pk)

        def line(role: str, target: str, source: Use = use, trigger_pk: int = pk) -> Use:
            return Use(
                role=role,
                name=source.name,
                enabled=source.enabled,
                service=None,
                characteristic=None,
                target=target,
                automation_pk=trigger_pk,
            )

        extra.extend(line("trigger", text) for text in trigger_texts(model.starts(pk)) or ("none",))
        extra.extend(line("condition", hit.text) for hit in model.conditions.get(pk, ()) if hit.accessory_pk != accessory_pk)
        extra.extend(line("turn off", event.text) for event in model.ends(pk))
    return tuple(extra) + uses


def _trigger_uses(context: _Context, item: Accessory) -> tuple[Use, ...]:
    """Automations this accessory starts, or turns off."""
    model = context.model
    found: list[Use] = []
    for event in model.events:
        if event.accessory_pk != item.pk:
            continue
        trigger = model.triggers.get(event.trigger_pk)
        prefix = "turns off when" if event.end else "when"
        service = event.service if event.service and event.service != item.display_name else None
        found.append(
            Use(
                role=trigger.kind if trigger else "event",
                name=event.automation,
                enabled=event.enabled,
                service=service,
                characteristic=None,
                target=f"{prefix} {event.detail}",
                automation_pk=event.trigger_pk,
            )
        )
    return _dedupe(tuple(found))


def _notifications(connection: sqlite3.Connection, context: _Context, accessory_pk: int) -> tuple[str, ...]:
    columns = context.bulletin_columns
    if not columns:
        return ()
    found: list[str] = []
    if "ZCHARACTERISTIC" in columns:
        rows = connection.execute(
            """
            SELECT ch.ZMANUFACTURERDESCRIPTION AS characteristic
            FROM ZMKFBULLETINREGISTRATION b
            JOIN ZMKFCHARACTERISTIC ch ON ch.Z_PK = b.ZCHARACTERISTIC
            JOIN ZMKFSERVICE s ON s.Z_PK = ch.ZSERVICE
            WHERE s.ZACCESSORY = ?
            """,
            (accessory_pk,),
        ).fetchall()
        for row in rows:
            label = text_or_none(row["characteristic"]) or "characteristic"
            if label not in found:
                found.append(label)
    for class_name, label in _CAMERA_BULLETINS.items():
        entity = context.model.schema.classes.get(class_name)
        column = context.camera_columns.get(entity) if entity is not None else None
        if column is None:
            continue
        row = connection.execute(
            f"""
            SELECT 1 FROM ZMKFBULLETINREGISTRATION
            WHERE Z_ENT = ? AND {quote_ident(column)} = ?
            """,
            (entity, accessory_pk),
        ).fetchone()
        if row is not None and label not in found:
            found.append(label)
    return tuple(found)


def _camera_columns(connection: sqlite3.Connection, model: Model, columns: frozenset[str]) -> dict[int, str]:
    """For each camera registration type, the accessory column its rows fill in this store."""
    found: dict[int, str] = {}
    for class_name in _CAMERA_BULLETINS:
        entity = model.schema.classes.get(class_name)
        if entity is None:
            continue
        for column in _BULLETIN_ACCESSORY_COLUMNS:
            if column not in columns:
                continue
            row = connection.execute(
                f"SELECT 1 FROM ZMKFBULLETINREGISTRATION WHERE Z_ENT = ? AND {quote_ident(column)} IS NOT NULL LIMIT 1",
                (entity,),
            ).fetchone()
            if row is not None:
                found[entity] = column
                break
    return found


def _shortcut_mentions(context: _Context, item: Accessory) -> tuple[tuple[Use, ...], tuple[Use, ...]]:
    names = item.own_names()
    if not names or not context.shortcuts:
        return (), ()
    longer = {name: _longer_names(name, context.known_names) for name in names}
    automations: list[Use] = []
    scenes: list[Use] = []
    for shortcut in context.shortcuts:
        if not any(_name_in_text(shortcut.text, name, longer[name]) for name in names):
            continue
        use = Use(role="shortcut", name="", enabled=None, service=None, characteristic=None, target=None)
        placed_automations, placed_scenes = _placed(context, shortcut.set_pk, use, None)
        automations.extend(placed_automations)
        scenes.extend(placed_scenes)
    return tuple(automations), tuple(scenes)


def _shortcut_text(data: bytes) -> str:
    """The strings in a shortcut. A binary plist may store a name as UTF-16, so search the decoded text."""
    try:
        loaded = plistlib.loads(data)
    except (ValueError, ExpatError):
        return data.decode("utf-8", errors="replace")
    strings: list[str] = []
    _collect_strings(loaded, strings, 0)
    return "\n".join(strings)


def _collect_strings(node: object, found: list[str], depth: int) -> None:
    if depth > 64:
        return
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            _collect_strings(key, found, depth + 1)
            _collect_strings(value, found, depth + 1)
    elif isinstance(node, list | tuple):
        for value in node:
            _collect_strings(value, found, depth + 1)
    elif isinstance(node, bytes) and node.startswith(b"bplist"):
        try:
            _collect_strings(plistlib.loads(node), found, depth + 1)
        except (ValueError, ExpatError):
            return


def _longer_names(name: str, known: tuple[str, ...]) -> tuple[str, ...]:
    """Known names that begin with name, such as "Outlet A S1" for "Outlet A"."""
    return tuple(other for other in known if len(other) > len(name) and other.startswith(name))


def _name_in_text(text: str, name: str, longer: tuple[str, ...] = ()) -> bool:
    """True when name occurs on its own, not as the start of a longer known name."""
    start = 0
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        before_ok = index == 0 or not text[index - 1].isalnum()
        after = index + len(name)
        after_ok = after >= len(text) or not text[after].isalnum()
        covered = any(text.startswith(extra, index) for extra in longer)
        if before_ok and after_ok and not covered:
            return True
        start = index + 1


def _dedupe(uses: tuple[Use, ...]) -> tuple[Use, ...]:
    unique = list(dict.fromkeys(uses))
    return tuple(
        sorted(unique, key=lambda use: (use.name.casefold(), use.automation_pk or 0, use.role, use.service or ""))
    )


def _known_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    found: set[str] = set()
    for row in connection.execute("SELECT ZCONFIGUREDNAME, ZPROVIDEDNAME FROM ZMKFACCESSORY"):
        found.update(text for text in map(text_or_none, row) if text)
    columns = table_columns(connection, "ZMKFSERVICE")
    service_names = ", ".join(
        column_or_null(columns, "s", column) for column in ("ZNAME", "ZEXPECTEDCONFIGUREDNAME", "ZPROVIDEDNAME")
    )
    for row in connection.execute(f"SELECT {service_names} FROM ZMKFSERVICE s"):
        found.update(text for text in map(text_or_none, row) if text)
    return tuple(sorted(found))
