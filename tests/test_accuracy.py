# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Answers stay right for OR conditions, linked scenes, shared names, and edge values."""

from __future__ import annotations

import io
import plistlib
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from homekit_map.cli import main
from homekit_map.day import format_day
from homekit_map.diff import format_diff
from homekit_map.errors import SnapshotError
from homekit_map.extract import write_catalog
from homekit_map.gaps import format_unused
from homekit_map.predicate import predicate_hits
from homekit_map.schema import ACTION_CLASS_LABELS, EVENT_CLASS_LABELS, TRIGGER_CLASS_LABELS, open_readonly
from tests.fixture import build_store

LESS_THAN = 0
EQUAL_TO = 4
NOT, AND, OR = 0, 1, 2


def archive(root: object) -> bytes:
    """Encode nested dicts and lists the way NSKeyedArchiver stores objects."""
    objects: list[object] = ["$null"]

    def add(value: object) -> object:
        if isinstance(value, dict):
            index = len(objects)
            objects.append(None)
            objects[index] = {key: add(item) for key, item in value.items()}
            return plistlib.UID(index)
        if isinstance(value, list):
            return [add(item) for item in value]
        return value

    top = add(root)
    return plistlib.dumps(
        {"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": top}, "$objects": objects},
        fmt=plistlib.FMT_BINARY,
    )


def kind(name: str) -> dict:
    return {"$classname": name}


def characteristic_test(operator: int, value: object) -> list[dict]:
    """The two comparisons Eve writes for one characteristic test."""
    identity = {"kAccessoryUUID": "00000000-0000-0000-0000-000000000001"}
    return [
        {
            "$class": kind("NSComparisonPredicate"),
            "NSLeftExpression": {"NSKeyPath": "characteristic"},
            "NSRightExpression": {"NSConstantValue": identity},
            "NSPredicateOperator": {"NSOperatorType": EQUAL_TO},
        },
        {
            "$class": kind("NSComparisonPredicate"),
            "NSLeftExpression": {"NSKeyPath": "characteristicValue"},
            "NSRightExpression": {"NSConstantValue": value},
            "NSPredicateOperator": {"NSOperatorType": operator},
        },
    ]


def compound(compound_type: int, parts: list[dict]) -> dict:
    return {"$class": kind("NSCompoundPredicate"), "NSCompoundPredicateType": compound_type, "NSSubpredicates": parts}


def texts(blob: bytes) -> list[str]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE ZMKFACCESSORY (Z_PK INTEGER)")
        return [hit.text for hit in predicate_hits(connection, blob)]
    finally:
        connection.close()


class ConditionTests(unittest.TestCase):
    def test_or_is_one_line_joined_by_or(self) -> None:
        blob = archive(compound(OR, [compound(AND, characteristic_test(EQUAL_TO, 1)), compound(AND, characteristic_test(EQUAL_TO, 0))]))
        self.assertEqual(texts(blob), ["characteristic is 1 or characteristic is 0"])

    def test_and_keeps_separate_tests(self) -> None:
        blob = archive(compound(AND, [compound(AND, characteristic_test(EQUAL_TO, 1)), compound(AND, characteristic_test(EQUAL_TO, 0))]))
        self.assertEqual(texts(blob), ["characteristic is 1", "characteristic is 0"])

    def test_not_is_negated(self) -> None:
        blob = archive(compound(NOT, [compound(AND, characteristic_test(EQUAL_TO, 1))]))
        self.assertEqual(texts(blob), ["not (characteristic is 1)"])

    def test_less_than_is_not_shown_as_is(self) -> None:
        blob = archive(compound(AND, characteristic_test(LESS_THAN, 18)))
        self.assertEqual(texts(blob), ["characteristic is less than 18"])

    def test_a_looping_or_broken_archive_is_skipped(self) -> None:
        looping = plistlib.dumps(
            {
                "$archiver": "NSKeyedArchiver",
                "$top": {"root": plistlib.UID(1)},
                "$objects": ["$null", {"self": plistlib.UID(1)}],
            },
            fmt=plistlib.FMT_BINARY,
        )
        broken = plistlib.dumps(
            {"$archiver": "NSKeyedArchiver", "$top": {"root": plistlib.UID(9)}, "$objects": ["$null"]},
            fmt=plistlib.FMT_BINARY,
        )
        self.assertEqual(texts(looping), [])
        self.assertEqual(texts(broken), [])


def search(database: Path, *words: str) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(["search", "--db", str(database), *words])
    assert status == 0
    return output.getvalue()


def section(text: str, heading: str) -> str:
    """The indented block under one heading of an accessory."""
    lines = text.splitlines()
    start = lines.index(f"  {heading}:") if f"  {heading}:" in lines else lines.index(f"  {heading}: none")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


class MapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Path(self._tmp.name) / "core.sqlite"
        build_store(self.database)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, *statements: str) -> None:
        connection = sqlite3.connect(self.database)
        try:
            for statement in statements:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()

    def test_scene_run_by_an_automation_stays_on_the_accessory(self) -> None:
        self.write("INSERT INTO Z_30TRIGGERS_ VALUES (3, 2)")
        text = search(self.database, "accessory", "Example", "Outlet")
        self.assertIn("    Away\n      action  service=Example Outlet  target=false", section(text, "scenes"))
        self.assertIn("      scene Away  service=Example Outlet  target=false", section(text, "automations"))

    def test_automations_with_the_same_name_stay_apart(self) -> None:
        self.write(
            "INSERT INTO ZMKFTRIGGER VALUES (3, 20, 'Evening', 'Evening', 0, 127)",
            "INSERT INTO ZMKFACTIONSET VALUES (4, '', 'HMActionSetTypeTriggerOwned')",
            "INSERT INTO Z_30TRIGGERS_ VALUES (4, 3)",
            "INSERT INTO ZMKFACTION VALUES (6, 10, 4, 2, 2, 1, x'08', NULL, NULL, NULL, NULL)",
        )
        automations = section(search(self.database, "accessory", "Example", "Outlet"), "automations")
        self.assertIn("    Evening [enabled]", automations)
        self.assertIn("    Evening [disabled]", automations)
        disabled = automations.split("    Evening [disabled]")[1]
        self.assertNotIn("Lamp", disabled)

    def test_empty_active_flag_counts_as_enabled_everywhere(self) -> None:
        self.write(
            "INSERT INTO Z_PRIMARYKEY VALUES (97, 'MKFSignificantTimeEvent')",
            "UPDATE ZMKFTRIGGER SET ZACTIVE = NULL WHERE Z_PK = 1",
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZSIGNIFICANTEVENT, ZOFFSETSECONDS) "
            "VALUES (2, 97, 1, 'sunset', 0)",
        )
        connection = open_readonly(self.database)
        try:
            day = format_day(connection)
            unused = format_unused(connection)
        finally:
            connection.close()
        self.assertIn("Evening [enabled]", day)
        self.assertNotIn("Evening", unused.split("disabled automations:")[1].split("scenes")[0])
        self.assertIn("  enabled", search(self.database, "automation", "Evening"))

    def test_sun_offsets_round_the_same_way_before_and_after(self) -> None:
        self.write(
            "INSERT INTO Z_PRIMARYKEY VALUES (97, 'MKFSignificantTimeEvent')",
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZSIGNIFICANTEVENT, ZOFFSETSECONDS) "
            "VALUES (2, 97, 1, 'sunset', -30)",
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZSIGNIFICANTEVENT, ZOFFSETSECONDS) "
            "VALUES (3, 97, 2, 'sunset', 30)",
        )
        connection = open_readonly(self.database)
        try:
            day = format_day(connection)
        finally:
            connection.close()
        self.assertIn("30 seconds before sunset", day)
        self.assertIn("30 seconds after sunset", day)

    def test_diff_reports_a_rename_and_a_move_together(self) -> None:
        newer_path = Path(self._tmp.name) / "newer.sqlite"
        build_store(newer_path)
        connection = sqlite3.connect(newer_path)
        connection.execute("INSERT INTO ZMKFROOM VALUES (2, 'Zone Two')")
        connection.execute("UPDATE ZMKFACCESSORY SET ZPROVIDEDNAME = 'New Lamp', ZROOM = 2 WHERE Z_PK = 4")
        connection.commit()
        connection.close()
        older = open_readonly(self.database)
        newer = open_readonly(newer_path)
        try:
            text = format_diff(older, newer)
        finally:
            older.close()
            newer.close()
        self.assertIn("renamed: Lamp -> New Lamp", text)
        self.assertIn("moved: New Lamp  Zone One -> Zone Two", text)


class LabelTests(unittest.TestCase):
    def test_labels_are_plain_words(self) -> None:
        for table in (ACTION_CLASS_LABELS, EVENT_CLASS_LABELS, TRIGGER_CLASS_LABELS):
            for class_name, label in table.items():
                with self.subTest(class_name=class_name):
                    self.assertRegex(label, r"^[a-z]+( [a-z]+)*$")


class ExportTests(unittest.TestCase):
    def test_export_is_private_and_leaves_no_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            output = Path(tmp) / "out" / "export.json"
            write_catalog(database, output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["export.json"])

    def test_export_refuses_a_symlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            elsewhere = Path(tmp) / "elsewhere.json"
            elsewhere.write_text("keep\n")
            output = Path(tmp) / "export.json"
            output.symlink_to(elsewhere)
            with self.assertRaises(SnapshotError):
                write_catalog(database, output)
            self.assertEqual(elsewhere.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
