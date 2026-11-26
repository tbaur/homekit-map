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

The words after `search` are one question, so quotes are optional. `search Outlet A` matches accessories, automations, scenes, and rooms. A first word of `room`, `accessory`, `automation`, `scene`, or `maker` limits that question. `search room Zone One` finds rooms. `search maker Example` finds that manufacturer. `search day` is the whole schedule, and `search day tuesday` keeps Tuesday. `search unused` lists what no automation or scene uses. `search Zone One` still matches every kind of name.

A search can return more than one kind of thing. Each kind is its own section.

## Accessory

```text
accessory 1: Example Outlet
  provided: Outlet B
  room: Zone One
  device: Example Strip
  automations:
    Evening [enabled]
      trigger  16:00
      condition  Power State is false
      condition  between sunset and sunrise
      action  service=Example Outlet  characteristic=Power State  target=true
      turn off  after 1 hour
      also:
        Outlet A
  scenes:
    Away
      action  service=Example Outlet  target=false
  triggers: none
  notifications:
    Power State
```

Each automation lists what starts it, its conditions, what it does to this accessory, and when it turns off. A `trigger` is a time, sunrise or sunset, presence, or an accessory, such as `Outlet A  Power State is true`. When more than three accessories start it the same way, they share one line with a count, such as `Leak Detected is true  (5 accessories)`. `trigger  none` means nothing starts the automation, so it never runs.

A condition must hold for the automation to run. A test on this accessory leaves out its name, so `Power State is false` means the automation does not run while this outlet is on. A test on another accessory starts with that accessory's name. A time condition reads like `between sunset and sunrise`, `between 19:00 and 01:00`, or `before 06:00`. Separate condition lines must all hold. Tests that HomeKit joins with OR share one line joined by `or`, and a negated test is shown as `not (...)`. A test this tool cannot read is shown as `other condition (not decoded)`, so a condition is never dropped without a trace.

`turn off` is the Home app's Turn Off setting, such as `after 1 hour`. `also` lists the other accessories that the same automation writes. When the automation writes one tile of an accessory that has several tiles, `also` uses the tile's name. Adaptive lighting is its own line, `adaptive lighting on` or `adaptive lighting off`. A play or pause action shows `media playback` on each speaker it controls. An action that comes from a scene the automation runs starts with `scene <name>`.

A `services` list follows the device line when the accessory has tiles with names of their own, such as the outlets of a power strip, or the temperature and humidity tiles of a sensor. A bridge shows `bridge:` and how many accessories it connects.

`triggers` lists the automations this accessory starts, such as `when Power State is true`. When this accessory ends a Turn Off, the line reads `turns off when ...`. When the tile that triggers has a name of its own, the line ends with `service=`.

Two automations with the same name are listed separately, each with its own state, conditions, and `also` list.

## Automation

```text
automation: Evening
  enabled
  type: event
  trigger:
    16:00
    Outlet A  Power State is true
  condition:
    Example Outlet  Power State is false
    between sunset and sunrise
  turn off:
    after 1 hour
  actions:
    Example Outlet  service=Example Outlet  characteristic=Power State  target=true
    scene Away  Lamp  adaptive lighting off
```

`trigger` lists every way the automation starts, one per line. A light sensor reads like `Current Light Level rises above 400 lux` or `drops below 50 lux`. A button reads like `Programmable Switch Event is single press`. `trigger: none` means nothing starts the automation. `turn off` appears when the automation has a Turn Off setting. An action that comes from a scene the automation runs starts with `scene <name>`. A play or pause action is listed once for each speaker, as `Player A  media playback`.

## Scene

```text
scene: Away
  actions:
    Example Outlet  service=Example Outlet  target=false
    Lamp  adaptive lighting off
  used by: none
```

Scenes are the ones the Home app shows, including the built-in scenes for arriving and leaving home. HomeKit also keeps a hidden action set for each automation, often named with a UUID. Those are not scenes, and a search does not return them.

## Room

```text
room: Zone One
  accessories:
    Example Outlet
    Example Strip
      Outlet C
      Outlet D
```

An accessory whose tiles have names of their own lists them under it.

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

A Turn Off time, such as `after 1 hour`, is when an automation ends, so the schedule leaves it out. A timer trigger from an older home shows its time on the automation, but it is not in the schedule.

A word that is not a day prints: `Give a weekday, such as tuesday, weekdays, or weekends.`

## Unused

```text
not in an automation or scene:
  Sensor  room=Zone One
disabled automations:
  Retired
scenes with no automation:
  Away
automations that do nothing:
  Retired  no trigger, no actions
```

An accessory counts as used when an automation or scene writes it, when it starts an automation, when a condition tests it, or when a shortcut names it. These are the same uses that `search` lists. A bridge with no tiles of its own is left off, because it never takes part in an automation. When an accessory's tiles have names of their own, its line ends with `services=`.

Scenes here are user-defined scenes that no automation runs. Built-in scenes are left off this list. An automation marked `no trigger` never starts. One marked `no actions` changes nothing when it does.

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

```text
compared: 20260101-000000 -> current
accessories:
  renamed: Lamp -> Example Lamp
  moved: Example Lamp  Zone One -> Zone Two
services:
  renamed: Outlet A -> Outlet C  accessory=Example Strip
rooms:
  added: Zone Two
automations:
  changed: Evening  enabled -> disabled
  edited: Morning
    - trigger  16:00
    + trigger  17:30
scenes:
  edited: Away
    - action  Lamp  adaptive lighting off
    + action  Lamp  adaptive lighting on
```

`diff` with no arguments compares the newest saved snapshot with `current`. One stamp compares that version with `current`. Two stamps compare those two versions. `diff versions` lists the saved copies, then `current`. A change is one of `added`, `removed`, `renamed`, `moved` (an accessory changed rooms), `changed` (an automation was enabled or disabled), or `edited` (something inside an automation or scene changed). An accessory that was renamed and moved gets both lines.

Under `edited`, a line that starts with `-` was there before, and a line that starts with `+` is there now. An edit can be a trigger, a condition, a Turn Off setting, an action, or a scene that an automation runs. Renaming an accessory does not mark the automations that use it as edited.

A service is one tile of an accessory, such as one outlet of a power strip. When a tile is renamed and its accessory keeps its name, the service line shows that accessory with `accessory=`. When an accessory and its only tile get the same new name, the rename is listed once, under accessories. A renamed room is one line under `rooms`, and its accessories are not listed as moved.

Objects are matched by HomeKit's model id, so a rename is not reported as a remove and an add. HomePods, Apple TVs, and AirPlay speakers are included. Scenes are the ones the Home app shows. The hidden action sets that automations own are left out.

If only one snapshot exists, `diff` says to run `backup` again. That backup keeps the current copy as the first version.

## Roles

| Line | Meaning |
|---|---|
| automation, `trigger` | What starts the automation, or `none`. |
| automation, `condition` | A test that must hold for the automation to run. |
| automation, `action` | The automation writes this accessory. |
| automation, `adaptive lighting` | The automation turns adaptive lighting on or off for this accessory. |
| automation, `media playback` | The automation plays or pauses audio on this speaker. |
| automation, `scene <name>` | The automation runs that scene, and the scene writes this accessory. |
| automation, `shortcut` | A shortcut in that automation names this accessory. |
| automation, `turn off` | The automation's Turn Off setting. |
| scene, `action` | A scene writes this accessory. The scene is listed whether or not an automation runs it. |
| triggers, `when ...` | This accessory starts the automation. |

`target=true` and `target=false` are power and other on or off values. Known states read as words, such as `single press`, `open`, `locked`, or `heat`. A number with `%` is a percentage, such as brightness. `°` is hue, `°C` is a temperature, and `lux` is a light level. Other numbers are shown as stored. A value that cannot be decoded is hex. The characteristic name comes from the service the write belongs to. It is omitted when that characteristic cannot be resolved.

A play or pause action does not say which of the two it is. HomeKit does not document how it stores that setting, so the tool does not guess.

## JSON

`homekit-map extract` writes the same facts for every accessory to `data/export.json`. The document has `configuredName`, `providedName`, `room`, `manufacturer`, `model`, `services`, `automations`, `scenes`, `triggers`, and `notifications`. `services` holds the names of the accessory's tiles. A use that comes from a scene that an automation runs has a `scene` field with that scene's name. The document does not include the filesystem path of the snapshot. The file is readable only by you (`0600`). It holds the names of your home, so keep it out of issues and repos.

Shortcut steps that point at an accessory only by an internal id are not listed. Those ids are not the accessory ids in the database. A shortcut is listed when the name of the accessory, or of one of its tiles, appears in the text of the shortcut. A name with accents still matches. The maker and model do not count.
