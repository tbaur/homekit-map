# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Command line for backup, extract, and accessory queries."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from homekit_map.about import describe, format_report, split_search
from homekit_map.backup import run_backup
from homekit_map.day import format_day, parse_weekday
from homekit_map.diff import diff_current, format_versions
from homekit_map.errors import SnapshotError
from homekit_map.extract import write_catalog
from homekit_map.gaps import format_unused
from homekit_map.paths import DEFAULT_SOURCE, default_export_path, default_snapshot_path
from homekit_map.schema import open_readonly

_SEARCH_FORMS = """\
search <name>
search day [tuesday]
search unused
search room <name>
search accessory <name>
search automation <name>
search scene <name>
search maker <name>
"""


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = _rewrite_command(list(argv))
    parser = argparse.ArgumentParser(
        prog="homekit-map",
        description="Map HomeKit: back up its database, then ask about the snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_SEARCH_FORMS + "diff\ndiff versions\n",
    )
    sub = parser.add_subparsers(dest="command")

    backup = sub.add_parser("backup", help="Copy the live database to data/core.sqlite")
    backup.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    backup.add_argument("--dest", type=Path, default=None)

    search = sub.add_parser("search", help="Ask about a name, a day, or what is unused")
    _add_search_words(search)

    compared = sub.add_parser("diff", help="Compare snapshots. diff versions lists them")
    compared.add_argument("versions", nargs="*", help="versions, or one or two saved stamps.")
    compared.add_argument("--db", type=Path, default=None)

    extract = sub.add_parser("extract", help="Write data/export.json from the snapshot")
    extract.add_argument("--db", type=Path, default=None)
    extract.add_argument("--output", type=Path, default=None)

    if argv and not argv[0].startswith("-") and argv[0] not in sub.choices:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "backup":
            dest = args.dest if args.dest is not None else default_snapshot_path()
            return run_backup(args.source, dest)
        if args.command == "extract":
            return _extract(args.db, args.output)
        if args.command == "search":
            text = _search_text(args.name)
            if not text.strip():
                sys.stdout.write(_SEARCH_FORMS)
                return 0
            return _print_search(args.db, text)
        if args.command == "diff":
            if args.versions == ["versions"]:
                sys.stdout.write(format_versions(args.db))
                return 0
            sys.stdout.write(diff_current(args.db, args.versions))
            return 0
    except SnapshotError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except sqlite3.OperationalError as exc:
        print(f"Could not read the snapshot: {exc}", file=sys.stderr)
        return 1
    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


def _rewrite_command(argv: list[str]) -> list[str]:
    """Keep old names working. Help only shows backup, search, diff, and extract."""
    if not argv or argv[0].startswith("-"):
        return argv
    if argv[0] in {"about", "uses"}:
        return ["search", *argv[1:]]
    if argv[0] == "day":
        return ["search", "day", *argv[1:]]
    if argv[0] == "unused":
        return ["search", "unused", *argv[1:]]
    if argv[0] == "accessories":
        return ["search", "accessory", *argv[1:]]
    if argv[0] == "versions":
        return ["diff", "versions", *argv[1:]]
    return argv


def _database(path: Path | None) -> Path:
    return path if path is not None else default_snapshot_path()


def _add_search_words(parser: argparse.ArgumentParser, dest: str = "name") -> None:
    """Take every remaining word as one search. Quotes stay optional."""
    parser.add_argument(
        dest,
        nargs="*",
        help="Words to search for. Quotes are optional.",
    )
    parser.add_argument("--db", type=Path, default=None)


def _search_text(words: list[str]) -> str:
    return " ".join(words)


def _extract(database: Path | None, output: Path | None) -> int:
    destination = output if output is not None else default_export_path()
    document = write_catalog(_database(database), destination)
    count = document["accessoryCount"]
    print(f"Wrote {destination.resolve()} ({count} accessories)", file=sys.stderr)
    return 0


def _print_search(database: Path | None, name: str) -> int:
    kind, needle = split_search(name)
    if kind == "day":
        return _print_day(database, needle)
    if kind == "unused":
        if needle.strip():
            print("search unused does not take a name.", file=sys.stderr)
            return 1
        return _print_query(database, format_unused)
    if not needle.strip():
        print("Give part of a name.", file=sys.stderr)
        return 1
    connection = open_readonly(_database(database))
    try:
        report = describe(connection, name)
    finally:
        connection.close()
    sys.stdout.write(format_report(report))
    return 0


def _print_day(database: Path | None, text: str) -> int:
    weekday = None
    if text.strip():
        weekday = parse_weekday(text)
        if weekday is None:
            print("Give a weekday, such as tuesday, weekdays, or weekends.", file=sys.stderr)
            return 1
    return _print_query(database, lambda connection: format_day(connection, weekday))


def _print_query(database: Path | None, render) -> int:
    connection = open_readonly(_database(database))
    try:
        text = render(connection)
    finally:
        connection.close()
    sys.stdout.write(text)
    return 0
