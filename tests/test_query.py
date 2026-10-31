# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Queries against a placeholder snapshot, never a live home."""

from __future__ import annotations

import io
import json
import plistlib
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from homekit_map.about import describe, format_report
from homekit_map.cli import main
from homekit_map.day import format_day
from homekit_map.diff import format_diff
from homekit_map.errors import SnapshotError
from homekit_map.extract import extract_catalog
from homekit_map.gaps import format_unused
from homekit_map.query import Accessory, Use, find_accessories, format_accessories
from homekit_map.schema import open_readonly
from tests.fixture import build_store


def _false_power_condition(model: uuid.UUID) -> bytes:
    """An Eve-style predicate: this characteristic's power state is false."""
    objects: list[object] = ["$null"]

    def add(value: object) -> plistlib.UID:
        objects.append(value)
        return plistlib.UID(len(objects) - 1)

    def classname(name: str) -> plistlib.UID:
        return add({"$classname": name, "$classes": [name, "NSObject"]})

    compound = classname("NSCompoundPredicate")
    array = classname("NSArray")
    comparison = classname("NSComparisonPredicate")
    constant = classname("NSConstantValueExpression")
    dictionary = classname("NSDictionary")
    operator = classname("NSEqualityPredicateOperator")
    identity = add(
        {
            "$class": dictionary,
            "NS.keys": ["kAccessoryUUID", "kServiceInstanceID", "kCharacteristicInstanceID"],
            "NS.objects": [str(model), 9, 1],
        }
    )
    left_identity = add(
        {"$class": comparison, "NSExpressionType": 3, "NSArguments": [{"NSKeyPath": "characteristic"}]}
    )
    left_value = add(
        {"$class": comparison, "NSExpressionType": 3, "NSArguments": [{"NSKeyPath": "characteristicValue"}]}
    )
    equal = add({"$class": operator, "NSOperatorType": 4, "NSNegate": False})
    first = add(
        {
            "$class": comparison,
            "NSLeftExpression": left_identity,
            "NSRightExpression": add({"$class": constant, "NSExpressionType": 0, "NSConstantValue": identity}),
            "NSPredicateOperator": equal,
        }
    )
    second = add(
        {
            "$class": comparison,
            "NSLeftExpression": left_value,
            "NSRightExpression": add({"$class": constant, "NSExpressionType": 0, "NSConstantValue": 0.0}),
            "NSPredicateOperator": equal,
        }
    )
    root = add(
        {
            "$class": compound,
            "NSCompoundPredicateType": 1,
            "NSSubpredicates": add({"$class": array, "NS.objects": [first, second]}),
        }
    )
    return plistlib.dumps(
        {"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": root}, "$objects": objects},
        fmt=plistlib.FMT_BINARY,
    )


class QueryTests(unittest.TestCase):
    def test_renamed_outlet_is_found_by_either_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            connection = open_readonly(database)
            try:
                by_old = find_accessories(connection, "Outlet B")
                by_new = find_accessories(connection, "Example Outlet")
                evening = format_report(describe(connection, "Evening"))
                away = format_report(describe(connection, "Away"))
                room = format_report(describe(connection, "Zone One"))
                limited = format_report(describe(connection, "room Zone One"))
            finally:
                connection.close()
            self.assertEqual([item.display_name for item in by_old], ["Example Outlet"])
            self.assertEqual([item.pk for item in by_old], [item.pk for item in by_new])
            listed = format_accessories(by_old)
            self.assertIn("Evening", listed)
            self.assertIn("target=true", listed)
            self.assertIn("Power State", listed)
            self.assertIn("Away", listed)
            self.assertIn("target=false", listed)
            self.assertIn("also:", listed)
            self.assertIn("Lamp", listed)
            self.assertIn("triggers: none", listed)

            self.assertIn("automation: Evening", evening)
            self.assertIn("Example Outlet", evening)
            self.assertIn("target=true", evening)
            self.assertNotIn("accessory 1:", evening)

            self.assertIn("scene: Away", away)
            self.assertIn("target=false", away)
            self.assertIn("used by: none", away)
            self.assertIn("Lamp  adaptive lighting off", away)
            self.assertNotIn("naturalLighting", away)

            self.assertIn("room: Zone One", room)
            self.assertIn("Outlet A", room)
            self.assertIn("Example Outlet", room)

            self.assertIn("room: Zone One", limited)
            self.assertNotIn("accessories: 0", limited)
            self.assertNotIn("automations: 0", limited)

    def test_about_joins_separate_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["search", "--db", str(database), "Example", "Outlet"])
            self.assertEqual(status, 0)
            self.assertIn("accessory 1: Example Outlet", output.getvalue())

    def test_no_command_prints_help(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main([])
        text = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("search", text)
        self.assertIn("backup", text)
        self.assertIn("search day", text)
        self.assertIn("search unused", text)
        self.assertIn("diff versions", text)
        self.assertNotIn("{backup,search,diff,extract,accessories", text)

    def test_unknown_command_prints_help(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["data"])
        text = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("{backup,search,diff,extract}", text)
        self.assertNotIn("invalid choice", text)

    def test_search_unused_and_old_names_still_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["search", "unused", "--db", str(database)])
            self.assertEqual(status, 0)
            self.assertIn("not in an automation", output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["unused", "--db", str(database)])
            self.assertEqual(status, 0)
            self.assertIn("disabled automations", output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["versions", "--db", str(database)])
            self.assertEqual(status, 0)
            self.assertIn("current", output.getvalue())

    def test_day_lists_sun_and_unused_lists_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            writable = sqlite3.connect(database)
            writable.execute("INSERT INTO Z_PRIMARYKEY VALUES (97, 'MKFSignificantTimeEvent')")
            writable.execute(
                """
                INSERT INTO ZMKFEVENT
                    (Z_PK, Z_ENT, ZTRIGGER, ZSIGNIFICANTEVENT, ZOFFSETSECONDS)
                VALUES (2, 97, 1, 'sunset', -600)
                """
            )
            writable.execute(
                "INSERT INTO ZMKFTRIGGER VALUES (4, 20, 'Retired', 'Retired', 0, 127)"
            )
            writable.commit()
            writable.close()
            connection = open_readonly(database)
            try:
                day = format_day(connection)
                unused = format_unused(connection)
                made = find_accessories(connection, "Example")
            finally:
                connection.close()
            self.assertIn("10 minutes before sunset", day)
            self.assertIn("Evening", day)
            self.assertIn("Outlet A S1", unused)
            self.assertIn("Retired", unused)
            self.assertIn("Away", unused)
            self.assertEqual(len(made), 4)

    def test_tuesday_hides_a_weekend_automation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            clock = plistlib.dumps(
                {
                    "$version": 100000,
                    "$archiver": "NSKeyedArchiver",
                    "$top": {"root": plistlib.UID(1)},
                    "$objects": [
                        "$null",
                        {
                            "$class": plistlib.UID(2),
                            "NS.hour": 10,
                            "NS.minute": 0,
                            "NS.leapMonth": 0,
                        },
                        {
                            "$classname": "NSDateComponents",
                            "$classes": ["NSDateComponents", "NSObject"],
                        },
                    ],
                },
                fmt=plistlib.FMT_BINARY,
            )
            writable = sqlite3.connect(database)
            writable.execute("INSERT INTO Z_PRIMARYKEY VALUES (90, 'MKFCalendarEvent')")
            writable.execute("INSERT INTO ZMKFTRIGGER VALUES (5, 20, 'Weekends', 'Weekends', 1, 65)")
            writable.execute("INSERT INTO ZMKFTRIGGER VALUES (6, 20, 'Daily', 'Daily', 1, 127)")
            writable.execute(
                "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZFIREDATECOMPONENTS) VALUES (3, 90, 5, ?)",
                (clock,),
            )
            writable.execute(
                "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZFIREDATECOMPONENTS) VALUES (4, 90, 6, ?)",
                (clock,),
            )
            writable.commit()
            writable.close()
            connection = open_readonly(database)
            try:
                tuesday = format_day(connection, 4)
                whole = format_day(connection)
            finally:
                connection.close()
            self.assertIn("day: tuesday", tuesday)
            self.assertIn("Daily", tuesday)
            self.assertNotIn("Weekends", tuesday)
            self.assertIn("weekends", whole)

    def test_diff_reports_a_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older_path = root / "older.sqlite"
            newer_path = root / "newer.sqlite"
            build_store(older_path)
            newer_path.write_bytes(older_path.read_bytes())
            writable = sqlite3.connect(newer_path)
            writable.execute("UPDATE ZMKFACTIONSET SET ZNAME = 'Gone' WHERE ZNAME = 'Away'")
            writable.execute("UPDATE ZMKFACCESSORY SET ZPROVIDEDNAME = 'New Lamp' WHERE ZPROVIDEDNAME = 'Lamp'")
            writable.commit()
            writable.close()
            older = open_readonly(older_path)
            newer = open_readonly(newer_path)
            try:
                text = format_diff(older, newer)
            finally:
                older.close()
                newer.close()
            self.assertIn("renamed: Away -> Gone", text)
            self.assertIn("renamed: Lamp -> New Lamp", text)

    def test_eve_condition_is_shown_on_the_automation_and_the_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            model = uuid.UUID("00000000-0000-0000-0000-000000000002")
            writable = sqlite3.connect(database)
            writable.execute("ALTER TABLE ZMKFACCESSORY ADD COLUMN ZMODELID BLOB")
            writable.execute("ALTER TABLE ZMKFSERVICE ADD COLUMN ZINSTANCEID INTEGER")
            writable.execute("ALTER TABLE ZMKFTRIGGER ADD COLUMN ZEVALUATIONCONDITION BLOB")
            writable.execute("UPDATE ZMKFACCESSORY SET ZMODELID = ? WHERE Z_PK = 2", (model.bytes,))
            writable.execute("UPDATE ZMKFSERVICE SET ZINSTANCEID = 9 WHERE Z_PK = 2")
            writable.execute(
                "UPDATE ZMKFTRIGGER SET ZEVALUATIONCONDITION = ? WHERE Z_PK = 1",
                (_false_power_condition(model),),
            )
            writable.commit()
            writable.close()
            connection = open_readonly(database)
            try:
                report = format_report(describe(connection, "Evening"))
                accessory = format_accessories(find_accessories(connection, "Example Outlet"))
            finally:
                connection.close()
            self.assertIn("  condition:\n    Example Outlet  Power State is false", report)
            self.assertIn("    Evening [enabled]\n      condition  Power State is false\n      action", accessory)
            self.assertNotIn("\n  conditions:\n", accessory)

    def test_actions_for_one_automation_share_a_heading(self) -> None:
        item = Accessory(
            pk=1,
            configured_name=None,
            provided_name="Outlet A",
            manufacturer=None,
            model=None,
            room="Zone One",
            services=(),
            automations=(
                Use("action", "Evening", True, "Outlet A", None, "0x01"),
                Use("action", "Evening", True, "Outlet A", None, "0x2b"),
                Use("action", "Morning", True, "Outlet A", None, "false"),
            ),
            scenes=(),
            triggers=(),
        )
        text = format_accessories([item])
        self.assertEqual(text.count("Evening"), 1)
        self.assertIn("    Evening [enabled]\n      action  service=Outlet A  target=0x01", text)
        self.assertIn("      action  service=Outlet A  target=0x2b", text)
        self.assertIn("    Morning [enabled]\n      action", text)

    def test_unused_family_member_is_listed_and_shorter_name_does_not_steal_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            connection = open_readonly(database)
            try:
                found = find_accessories(connection, "Outlet A")
            finally:
                connection.close()
            by_name = {item.display_name: item for item in found}
            self.assertEqual(set(by_name), {"Outlet A", "Outlet A S1"})
            self.assertEqual(by_name["Outlet A"].triggers[0].role, "event")
            self.assertEqual(by_name["Outlet A"].triggers[0].name, "Morning")
            self.assertEqual(by_name["Outlet A"].automations, ())
            self.assertEqual(by_name["Outlet A S1"].automations[0].role, "shortcut")
            self.assertEqual(by_name["Outlet A S1"].automations[0].name, "Morning")
            self.assertEqual(by_name["Outlet A"].scenes, ())

    def test_catalog_omits_the_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "core.sqlite"
            build_store(database)
            document = extract_catalog(database)
            encoded = json.dumps(document)
            self.assertNotIn("databasePath", encoded)
            self.assertNotIn("/Users", encoded)
            self.assertEqual(document["accessoryCount"], 4)
            renamed = next(
                item
                for item in document["accessories"]
                if isinstance(item, dict) and item.get("providedName") == "Outlet B"
            )
            self.assertEqual(renamed["configuredName"], "Example Outlet")

    def test_refuses_to_open_the_live_homekit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "Library" / "HomeKit"
            live.mkdir(parents=True)
            database = live / "core.sqlite"
            sqlite3.connect(database).close()
            with (
                patch("homekit_map.schema.LIVE_HOMEKIT_DIR", live),
                self.assertRaises(SnapshotError),
            ):
                open_readonly(database)
            self.assertTrue(database.is_file())
            self.assertFalse((live / "core.sqlite-wal").exists())


if __name__ == "__main__":
    unittest.main()
