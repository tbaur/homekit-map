# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Triggers, conditions, Turn Off, speakers, tiles, bridges, and idle automations. Placeholder data only."""

from __future__ import annotations

import io
import plistlib
import sqlite3
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from homekit_map.cli import main
from homekit_map.day import format_day
from homekit_map.gaps import format_unused
from homekit_map.predicate import predicate_hits
from homekit_map.schema import open_readonly
from tests.fixture import (
    ACCESSORY_INFORMATION,
    LIGHT_LEVEL,
    OUTLET,
    POWER_STATE,
    SWITCH_EVENT,
    archive,
    build_store,
    clock_archive,
    compound,
    kind,
)

AND, OR = 1, 2
LESS, AT_MOST, GREATER, AT_LEAST, EQUAL = 0, 1, 2, 3, 4


def now() -> dict[str, object]:
    return {"$class": kind("NSFunctionExpression"), "NSSelectorName": "now", "NSArguments": []}


def sun(name: str) -> dict[str, object]:
    specifier = {"$class": kind("NSKeyPathSpecifierExpression"), "NSKeyPath": name}
    return {"$class": kind("NSKeyPathExpression"), "NSSelectorName": "valueForKey:", "NSArguments": [specifier]}


def clock(hour: int, minute: int) -> dict[str, object]:
    components = {"$class": kind("NSDateComponents"), "NS.hour": hour, "NS.minute": minute}
    return {"$class": kind("NSConstantValueExpression"), "NSConstantValue": components}


def plus(base: dict[str, object], hours: int) -> dict[str, object]:
    return {"$class": kind("NSFunctionExpression"), "NSSelectorName": "add:to:", "NSArguments": [base, clock(hours, 0)]}


def compare(left: dict[str, object], operator: int, right: object) -> dict[str, object]:
    return {
        "$class": kind("NSComparisonPredicate"),
        "NSLeftExpression": left,
        "NSRightExpression": right,
        "NSPredicateOperator": {"$class": kind("NSComparisonPredicateOperator"), "NSOperatorType": operator},
    }


def condition_texts(root: dict[str, object]) -> list[str]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE ZMKFACCESSORY (Z_PK INTEGER)")
        return [hit.text for hit in predicate_hits(connection, archive(root))]
    finally:
        connection.close()


NIGHT = compound(OR, [compare(sun("sunset"), AT_MOST, now()), compare(sun("sunrise"), GREATER, now())])


class TimeConditionTests(unittest.TestCase):
    def test_sunset_to_sunrise(self) -> None:
        self.assertEqual(condition_texts(NIGHT), ["between sunset and sunrise"])

    def test_clock_window_over_midnight(self) -> None:
        root = compound(OR, [compare(now(), GREATER, clock(19, 0)), compare(now(), LESS, clock(1, 0))])
        self.assertEqual(condition_texts(root), ["between 19:00 and 01:00"])

    def test_clock_window_in_one_day(self) -> None:
        root = compound(AND, [compare(now(), AT_LEAST, clock(8, 0)), compare(now(), AT_MOST, clock(17, 30))])
        self.assertEqual(condition_texts(root), ["between 08:00 and 17:30"])

    def test_offset_from_sunrise(self) -> None:
        root = compound(
            AND, [compare(sun("sunrise"), GREATER, plus(now(), 4)), compare(sun("sunset"), AT_MOST, now())]
        )
        self.assertEqual(condition_texts(root), ["between sunset and 4 hours before sunrise"])

    def test_one_sided_time(self) -> None:
        self.assertEqual(condition_texts(compare(now(), LESS, clock(6, 0))), ["before 06:00"])

    def test_unknown_test_is_reported_not_dropped(self) -> None:
        root = compare({"$class": kind("NSKeyPathExpression"), "NSKeyPath": "presence"}, EQUAL, {"NSConstantValue": 1})
        self.assertEqual(condition_texts(root), ["other condition (not decoded)"])

    def test_time_window_and_characteristic_test_are_separate_lines(self) -> None:
        identity = {"kAccessoryUUID": "00000000-0000-0000-0000-000000000001"}
        test = compound(
            AND,
            [
                compare({"NSKeyPath": "characteristic"}, EQUAL, {"NSConstantValue": identity}),
                compare({"NSKeyPath": "characteristicValue"}, EQUAL, {"NSConstantValue": 1}),
            ],
        )
        self.assertEqual(condition_texts(compound(AND, [NIGHT, test])), ["between sunset and sunrise", "characteristic is 1"])


class MapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Path(self._tmp.name) / "core.sqlite"
        build_store(self.database)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, *statements: str, values: tuple[object, ...] = ()) -> None:
        connection = sqlite3.connect(self.database)
        try:
            for statement in statements:
                connection.execute(statement, values if "?" in statement else ())
            connection.commit()
        finally:
            connection.close()

    def run_cli(self, *words: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main([*words, "--db", str(self.database)])
        self.assertEqual(status, 0)
        return output.getvalue()

    def unused(self) -> str:
        connection = open_readonly(self.database)
        try:
            return format_unused(connection)
        finally:
            connection.close()

    def test_trigger_value_and_turn_off(self) -> None:
        self.write(
            "INSERT INTO Z_PRIMARYKEY VALUES (94, 'MKFDurationEvent')",
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZDURATION, ZENDEVENT) VALUES (11, 94, 2, 3600, 1)",
        )
        automation = self.run_cli("search", "automation", "Morning")
        self.assertIn("  trigger:\n    Outlet A  Power State is true\n  turn off:\n    after 1 hour\n  actions:", automation)
        accessory = self.run_cli("search", "accessory", "Outlet A S1")
        self.assertIn(
            "    Morning [enabled]\n      trigger  Outlet A  Power State is true\n      shortcut\n      turn off  after 1 hour",
            accessory,
        )
        self.assertIn("  triggers:\n    Morning [enabled]\n      when Power State is true", self.run_cli("search", "accessory", "Outlet A"))
        connection = open_readonly(self.database)
        try:
            day = format_day(connection)
        finally:
            connection.close()
        self.assertNotIn("duration", day)
        self.assertNotIn("Morning", day)

    def test_time_condition_shows_on_each_accessory_of_the_automation(self) -> None:
        self.write(
            "ALTER TABLE ZMKFTRIGGER ADD COLUMN ZEVALUATIONCONDITION BLOB",
            "UPDATE ZMKFTRIGGER SET ZEVALUATIONCONDITION = ? WHERE Z_PK = 1",
            values=(archive(NIGHT),),
        )
        self.assertIn("  condition:\n    between sunset and sunrise\n", self.run_cli("search", "automation", "Evening"))
        lamp = self.run_cli("search", "accessory", "Lamp")
        self.assertIn("    Evening [enabled]\n      trigger  16:00\n      condition  between sunset and sunrise\n      action", lamp)

    def test_automation_without_a_start(self) -> None:
        self.write("DELETE FROM ZMKFEVENT WHERE Z_PK = 10")
        self.assertIn("  type: event\n  trigger: none\n", self.run_cli("search", "automation", "Evening"))
        self.assertIn("    Evening [enabled]\n      trigger  none\n", self.run_cli("search", "accessory", "Lamp"))
        self.assertIn("automations that do nothing:\n  Evening  no trigger\n", self.unused())

    def test_light_level_thresholds(self) -> None:
        self.write(
            "INSERT INTO Z_PRIMARYKEY VALUES (92, 'MKFCharacteristicRangeEvent')",
            "INSERT INTO ZMKFACCESSORY VALUES (5, NULL, 'Sensor', NULL, NULL, 1)",
            "INSERT INTO ZMKFSERVICE VALUES (5, 'Sensor', NULL, 5, 'Sensor', 9, NULL)",
            "INSERT INTO ZMKFTRIGGER VALUES (7, 20, 'Range One', 'Range One', 1, 127)",
            "INSERT INTO ZMKFTRIGGER VALUES (8, 20, 'Range Two', 'Range Two', 1, 127)",
        )
        self.write(
            "INSERT INTO ZMKFCHARACTERISTIC VALUES (3, 5, 'Current Light Level', 'float', 'lux', 1, ?, 1.0, 100000.0)",
            values=(LIGHT_LEVEL,),
        )
        rises = (b"\x35" + struct.pack("<f", 410.996), b"\x35" + struct.pack("<f", 100000.0))
        drops = (b"\x36" + struct.pack("<d", 1.0), b"\x35" + struct.pack("<f", 390.996))
        for pk, trigger, (low, high) in ((12, 7, rises), (13, 8, drops)):
            self.write(
                "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZSERVICE, ZCHARACTERISTICID, ZENDEVENT, ZMIN, ZMAX) "
                f"VALUES ({pk}, 92, {trigger}, 5, 1, 0, ?, ?)",
                values=(low, high),
            )
        self.assertIn("    Sensor  Current Light Level rises above 411 lux\n", self.run_cli("search", "automation", "Range One"))
        self.assertIn("    Sensor  Current Light Level drops below 391 lux\n", self.run_cli("search", "automation", "Range Two"))

    def test_button_press_reads_as_words(self) -> None:
        self.write(
            "INSERT INTO ZMKFCHARACTERISTIC VALUES (4, 1, 'Programmable Switch Event', 'uint8', NULL, 2, ?, NULL, NULL)",
            values=(SWITCH_EVENT,),
        )
        self.write("UPDATE ZMKFEVENT SET ZCHARACTERISTICID = 2, ZEVENTVALUE = x'08' WHERE Z_PK = 1")
        self.assertIn("    Outlet A  Programmable Switch Event is single press\n", self.run_cli("search", "automation", "Morning"))

    def test_speaker_in_a_media_action(self) -> None:
        self.write(
            "INSERT INTO ZMKFACCESSORY VALUES (6, 'Player A', 'Player', NULL, NULL, 1)",
            "INSERT INTO ZMKFACTION VALUES (6, 38, 3, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            "INSERT INTO Z_26ACTIONMEDIAPLAYBACKS_ VALUES (6, 6)",
        )
        self.assertIn("    Player A  media playback\n", self.run_cli("search", "scene", "Away"))
        self.assertIn("  scenes:\n    Away\n      media playback\n", self.run_cli("search", "accessory", "Player A"))
        self.assertNotIn("Player A", self.unused())

    def test_bridge_tiles_and_companions(self) -> None:
        self.write(
            "ALTER TABLE ZMKFACCESSORY ADD COLUMN ZHOSTACCESSORY INTEGER",
            "INSERT INTO ZMKFACCESSORY (Z_PK, ZPROVIDEDNAME, ZROOM) VALUES (7, 'Example Bridge', 1)",
            "INSERT INTO ZMKFACCESSORY (Z_PK, ZPROVIDEDNAME, ZROOM, ZHOSTACCESSORY) VALUES (8, 'Example Strip', 1, 7)",
        )
        connection = sqlite3.connect(self.database)
        try:
            connection.executemany(
                "INSERT INTO ZMKFSERVICE VALUES (?, ?, NULL, ?, ?, ?, ?)",
                [
                    (10, None, 7, "Example Bridge", 1, ACCESSORY_INFORMATION),
                    (11, "Outlet C", 8, "Outlet", 2, OUTLET),
                    (12, "Outlet D", 8, "Outlet", 3, OUTLET),
                ],
            )
            connection.execute(
                "INSERT INTO ZMKFCHARACTERISTIC VALUES (5, 11, 'Power State', 'bool', NULL, 1, ?, NULL, NULL)",
                (POWER_STATE,),
            )
            connection.execute("INSERT INTO ZMKFACTION VALUES (7, 10, 1, 8, 11, 1, x'01', NULL, NULL, NULL, NULL)")
            connection.commit()
        finally:
            connection.close()
        strip = self.run_cli("search", "accessory", "Example Strip")
        self.assertIn("  services:\n    Outlet C\n    Outlet D\n", strip)
        self.assertIn("characteristic=Power State  target=true", strip)
        self.assertIn("  bridge: 1 accessory\n", self.run_cli("search", "accessory", "Example Bridge"))
        outlet = self.run_cli("search", "accessory", "Example Outlet")
        self.assertIn("      also:\n        Lamp\n        Outlet C\n", outlet)
        self.assertIn("    Example Strip\n      Outlet C\n      Outlet D\n", self.run_cli("search", "room", "Zone One"))
        unused = self.unused()
        self.assertNotIn("Example Bridge", unused)
        self.assertNotIn("Example Strip", unused)

    def test_scene_search_skips_hidden_sets_and_keeps_built_in_scenes(self) -> None:
        self.write(
            "INSERT INTO ZMKFACTIONSET VALUES (5, '00000000-0000-0000-0000-00000000000A', 'HMActionSetTypeTriggerOwned')",
            "INSERT INTO ZMKFACTIONSET VALUES (6, 'Example Arrival', 'HMActionSetTypeHomeArrival')",
        )
        self.assertEqual(self.run_cli("search", "scene", "0000"), "scenes: 0\n")
        self.assertIn("scene: Example Arrival\n", self.run_cli("search", "scene", "Example"))

    def test_shortcut_names_are_read_from_the_plist(self) -> None:
        payload = plistlib.dumps({"WFWorkflowActions": [{"WFAccessory": "Outlet \u00c9"}]}, fmt=plistlib.FMT_BINARY)
        self.assertNotIn("Outlet \u00c9".encode(), payload)
        self.write("UPDATE ZMKFACCESSORY SET ZCONFIGUREDNAME = 'Outlet \u00c9' WHERE Z_PK = 3")
        self.write("UPDATE ZMKFACTION SET ZDATA = ? WHERE Z_PK = 3", values=(payload,))
        self.assertIn("      shortcut\n", self.run_cli("search", "accessory", "Outlet \u00c9"))

    def test_maker_search_finds_the_maker(self) -> None:
        text = self.run_cli("search", "maker", "Example")
        self.assertTrue(text.startswith("accessories: 4\n"))
        self.assertEqual(self.run_cli("search", "maker", "Outlet"), "makers: 0\n")

    def test_a_maker_name_in_a_shortcut_is_not_a_use(self) -> None:
        self.write("UPDATE ZMKFACTION SET ZDATA = ? WHERE Z_PK = 3", values=(b"Example only",))
        text = self.run_cli("search", "maker", "Example")
        self.assertTrue(text.startswith("accessories: 4\n"))
        self.assertNotIn("shortcut", text)

    def test_camera_notification_column_is_found_per_store(self) -> None:
        self.write(
            "INSERT INTO Z_PRIMARYKEY VALUES (48, 'MKFCameraAccessModeBulletinRegistration')",
            "CREATE TABLE ZMKFBULLETINREGISTRATION (Z_PK INTEGER, Z_ENT INTEGER, ZACCESSORY INTEGER, "
            "ZACCESSORY1 INTEGER, ZACCESSORY2 INTEGER, ZACCESSORY3 INTEGER, ZCHARACTERISTIC INTEGER)",
            "INSERT INTO ZMKFBULLETINREGISTRATION VALUES (1, 48, NULL, NULL, NULL, 4, NULL)",
        )
        self.assertIn("  notifications:\n    camera access mode\n", self.run_cli("search", "accessory", "Lamp"))

    def test_timer_trigger_shows_its_time(self) -> None:
        self.write(
            "ALTER TABLE ZMKFTRIGGER ADD COLUMN ZSIGNIFICANTEVENT TEXT",
            "ALTER TABLE ZMKFTRIGGER ADD COLUMN ZSIGNIFICANTEVENTOFFSET BLOB",
            "ALTER TABLE ZMKFTRIGGER ADD COLUMN ZFIREDATE REAL",
        )
        self.write(
            "INSERT INTO ZMKFTRIGGER (Z_PK, Z_ENT, ZNAME, ZACTIVE, ZSIGNIFICANTEVENT, ZSIGNIFICANTEVENTOFFSET) "
            "VALUES (9, 21, 'Range Timer', 1, 'sunset', ?)",
            values=(clock_archive(0, 30),),
        )
        self.assertIn("  type: timer\n  trigger:\n    30 minutes after sunset\n", self.run_cli("search", "automation", "Range Timer"))
        self.assertIn("  Range Timer  no actions\n", self.unused())


if __name__ == "__main__":
    unittest.main()
