# Querying the snapshot

Run these with `bin/homekit-map`. The working directory does not matter. With no command, or a command the tool does not know, it prints help.

```bash
bin/homekit-map search Outlet A
bin/homekit-map search day tuesday
bin/homekit-map search unused
bin/homekit-map diff
bin/homekit-map diff versions
bin/homekit-map extract
```

The words after `search` are one question, so quotes are optional. `search Outlet A` matches accessories, automations, scenes, and rooms. A first word of `room`, `accessory`, `automation`, `scene`, or `maker` limits that question. `search room Zone One` finds rooms. `search maker Example` finds that manufacturer. `search day` is the whole schedule, and `search day tuesday` keeps Tuesday. `search unused` lists what no automation runs. `search Zone One` still matches every kind of name.

A search can return more than one kind of thing. Each kind is its own section.

## Accessory

```text
accessory 1: Example Outlet
  provided: Outlet B
  room: Zone One
  device: Example Strip
  automations:
    Evening [enabled]
      condition  Power State is false
      action  service=Example Outlet  characteristic=Power State  target=true
      also:
        Outlet A
  scenes:
    Away
      action  service=Example Outlet  target=false
  triggers: none
  notifications:
    Power State
```

`also` lists the other accessories that same automation writes. Adaptive lighting is its own line, `adaptive lighting on` or `adaptive lighting off`, on the accessory it controls. A condition is the HomeKit predicate, after the trigger and before the accessories it runs. `Power State is false` means the automation does not run while that switch is on. Separate condition lines must all hold. Tests that HomeKit joins with OR share one line joined by `or`, and a negated test is shown as `not (...)`.

Two automations with the same name are listed separately, each with its own state, conditions, and `also` list.

## Automation

```text
automation: Evening
  enabled
  type: event
  trigger:
    16:00
  condition:
    Example Outlet  Power State is false
  actions:
    Example Outlet  service=Example Outlet  characteristic=Power State  target=true
    Lamp  adaptive lighting off
```

## Scene

```text
scene: Away
  actions:
    Example Outlet  service=Example Outlet  target=false
    Lamp  adaptive lighting off
  used by: none
```

## Room

```text
room: Zone One
  accessories:
    Example Outlet
    Outlet A
```

A family of outlets that share a prefix, such as "Outlet A" and "Outlet A S1", all match a search for the shorter text. Each one is a separate accessory, with its own uses. A shortcut that only names "Outlet A S1" is not also reported for "Outlet A".

## Day

```text
day: tuesday
  16:00
    Evening [enabled]
  sunset
    Morning [enabled]
```

`search day` with no weekday is the whole schedule. `search day tuesday`, `search day weekends`, and `search day weekdays` filter it. A full name or a short one such as `tue` is enough. Daily automations are included. An automation that is not limited by weekday is included too. A weekend-only automation is left off a Tuesday listing. The full schedule prints `weekends` or `weekdays` on an automation that is limited to those days.

A word that is not a day prints: `Give a weekday, such as tuesday, weekdays, or weekends.`

## Unused

```text
not in an automation:
  Outlet A  room=Zone One  device=Example Strip
disabled automations:
  Retired
scenes with no automation:
  Away
```

Scenes here are user-defined scenes that no automation runs. Built-in scenes are left off this list.

## Versions and diff

```text
versions:
  20260101-000000
  current
```

```text
compared: 20260101-000000 -> current
no changes
```

`diff` with no arguments compares the newest saved snapshot with `current`. One stamp compares that version with `current`. Two stamps compare those two versions. `diff versions` lists the saved copies, then `current`. A change is one of `added`, `removed`, `renamed`, `moved` (an accessory changed rooms), or `changed` (an automation was enabled or disabled). An accessory that was renamed and moved gets both lines.

If only one snapshot exists, `diff` says to run `backup` again. That backup keeps the current copy as the first version.

## Roles

| Line | Meaning |
|---|---|
| automation, role `action` | The automation writes this accessory. |
| automation, role `adaptive lighting` | The automation turns adaptive lighting on or off for this accessory. |
| automation, role `scene` | The automation runs a scene, shown as `scene <name>`, and that scene writes this accessory. |
| automation, role `shortcut` | A shortcut in that automation contains this accessory's name as text. |
| scene, role `action` | A scene writes this accessory. The scene is listed whether or not an automation runs it. |
| trigger, role `event` or `timer` | This accessory starts the automation. |

`target=true` and `target=false` are power and other bool writes. A number with `%` is a percentage, such as brightness. `°` is hue. Other numbers are shown as stored. A value that cannot be decoded is hex. The characteristic name comes from the service the write belongs to. It is omitted when that characteristic cannot be resolved.

## JSON

`homekit-map extract` writes the same facts for every accessory to `data/export.json`. The document has `configuredName`, `providedName`, `room`, `manufacturer`, `model`, `services`, `automations`, `scenes`, `triggers`, and `notifications`. It does not include the filesystem path of the snapshot. The file is readable only by you (`0600`). It holds the names of your home, so keep it out of issues and repos.

Shortcut steps that point at an accessory only by an internal id are not listed. Those ids are not the accessory ids in the database. A shortcut is listed when the accessory's own name appears in the shortcut payload.
