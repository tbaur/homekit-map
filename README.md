# homekit-map

[![Tests](https://github.com/tbaur/homekit-map/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/tbaur/homekit-map/actions/workflows/test.yml?query=branch%3Amain)
[![CodeQL](https://github.com/tbaur/homekit-map/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/tbaur/homekit-map/actions/workflows/codeql.yml?query=branch%3Amain)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](pyproject.toml)

A map of HomeKit: its accessories, rooms, automations, and scenes, and how they connect.

## Why it exists

The Home app shows one thing at a time. You can open an automation and see what it does. You cannot ask the reverse: which automations turn this light on, what runs on a Tuesday evening, or which accessories nothing uses. Once a home has a few dozen accessories and automations, it gets hard to know why a light came on, or what breaks if you remove a device. The data to answer that already exists in HomeKit's database on your Mac.

## What it does

homekit-map copies that database without writing to it, then answers questions from the copy:

- **Search** any accessory, room, automation, or scene. An accessory shows every automation and scene that touches it, with the trigger, the conditions, and the value it sets.
- **Schedule** lists what runs by time of day, sunrise, sunset, and presence, for the week or a day.
- **Unused** lists accessories that no automation uses, automations that are turned off, and scenes nothing runs.
- **Diff** shows what changed between two backups, so you can see what an edit in the Home app actually did.
- **Extract** writes the whole map to JSON for your own scripts.

It only reads. It never changes your home, and it never talks to your accessories or to Apple.

## Where the data lives

The live database stays in `~/Library/HomeKit`. Commands after `backup` read `data/` next to this project. `data/` is gitignored. It contains pairing material and the names of your rooms and accessories.

## Setup

You need macOS with the Home app set up, and Python 3.11 or newer. The tool has no runtime dependencies.

The terminal that runs `backup` needs Full Disk Access (System Settings, Privacy & Security, Full Disk Access).

```bash
git clone https://github.com/tbaur/homekit-map.git
cd homekit-map
make install
bin/homekit-map backup
```

`make install` creates `.venv` in this directory and installs the tool there. `bin/homekit-map` does that install itself the first time you run it. You can start it from any directory. `.venv` is gitignored. Run `backup` again later to refresh the snapshot. The first backup that finds an existing snapshot keeps that copy in `data/versions/`. Later backups add a version only when the database has changed. A backup will not replace the live database.

## Example

```text
$ bin/homekit-map search accessory Example Outlet
accessories: 1

accessory 1: Example Outlet
  provided: Outlet B
  room: Zone One
  device: Example Strip
  automations:
    Evening [enabled]
      action  service=Example Outlet  characteristic=Power State  target=true
      also:
        Lamp
  scenes:
    Away
      action  service=Example Outlet  target=false
  triggers: none
  notifications: none
```

The Evening automation turns this outlet on and also controls Lamp. The Away scene turns it off. Nothing triggers on it, and it sends no notifications. `provided` is the name the accessory reported before it was renamed in the Home app. [docs/query.md](docs/query.md) has examples of every kind of answer.

## Search

```bash
bin/homekit-map search Outlet A
bin/homekit-map search day tuesday
bin/homekit-map search unused
```

The words after `search` are the question, so quotes are optional. `search Outlet A` looks across accessories, automations, scenes, and rooms. A first word of `room`, `accessory`, `automation`, `scene`, or `maker` limits that question. `search room Zone One` finds the Zone One room. `search day` is the schedule, and `search day tuesday` keeps the automations that run on Tuesday. `search unused` is accessories in no automation, disabled automations, and scenes nothing runs.

An accessory hit lists each automation once. The automation lists its trigger, then its condition, then that accessory's actions and the other accessories it writes. Scenes, triggers, and notifications are separate. An automation hit uses the same order: what starts it, its conditions, and what it writes. A scene hit lists its actions and which automations run it. A room hit lists the accessories in that room.

`search day` is the clock, sunrise, sunset, presence, and durations. Daily automations are included in a weekday. A weekend-only automation is not. The full schedule labels weekends and weekdays when an automation is limited to those days.

## Diff

```bash
bin/homekit-map diff
bin/homekit-map diff versions
```

`diff` compares the current snapshot with the newest file in `data/versions/`. Pass one stamp to compare that version with current, or two stamps to compare those versions. `diff versions` lists the saved copies, then `current`. When nothing has changed, `diff` says `no changes`.

## Extract

```bash
bin/homekit-map extract
```

That writes `data/export.json`. The file does not record the path of the database.

## What a value means

`true` and `false` are power and other bool writes. A number with `%` is a percentage, such as brightness. `°` is hue. Other numbers are shown as stored. A characteristic name comes from the service the write belongs to.

Shortcut workflows that refer to an accessory only by an internal id are not resolved. Those ids do not match the accessory ids in the database. If the shortcut text contains the name, the hit is still reported.

## Commands

| Command | Reads | Writes |
|---|---|---|
| `backup` | the live database, read-only | `data/core.sqlite`, and `data/versions/` when it keeps a copy |
| `search` | the snapshot | nothing |
| `diff` | two snapshots, or the version list | nothing |
| `extract` | the snapshot | `data/export.json` |

Running `homekit-map` with no command, or with a command it does not know, prints help. `search` with no words prints the questions it accepts.

`search`, `diff`, and `extract` refuse to open `~/Library/HomeKit`. Pass `--db`, and `--output` or `--dest` when a command writes, to use another snapshot path. The destination still cannot be inside the live HomeKit folder.

Details of why the copy is safe are in [docs/safety.md](docs/safety.md). The query fields are in [docs/query.md](docs/query.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks, and PR titles, and [RELEASING.md](RELEASING.md) for how releases are cut. Report security issues as described in [SECURITY.md](SECURITY.md).

## License

Copyright 2026 tbaur

Licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for details.
