# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""diff reports renames wherever HomeKit stores the name. Placeholder data only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from homekit_map.diff import format_diff
from homekit_map.schema import open_readonly
from tests.fixture import ACCESSORY_INFORMATION, OUTLET, build_store, clock_archive


def model_id(table_number: int, pk: int) -> bytes:
    return uuid.UUID(int=table_number * 1000 + pk).bytes


def add_real_columns(path: Path) -> None:
    """Add the model ids a real snapshot has, then a speaker and a two-outlet strip."""
    connection = sqlite3.connect(path)
    try:
        for number, table in enumerate(("ZMKFACCESSORY", "ZMKFSERVICE", "ZMKFROOM", "ZMKFTRIGGER", "ZMKFACTIONSET")):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN ZMODELID BLOB")
            for (pk,) in connection.execute(f"SELECT Z_PK FROM {table}").fetchall():
                connection.execute(f"UPDATE {table} SET ZMODELID = ? WHERE Z_PK = ?", (model_id(number, pk), pk))
        connection.execute("ALTER TABLE ZMKFACCESSORY ADD COLUMN ZUNIQUEIDENTIFIER TEXT")
        connection.execute("UPDATE ZMKFACCESSORY SET ZUNIQUEIDENTIFIER = 'SERIAL-000' || Z_PK")
        connection.execute(
            "INSERT INTO ZMKFACCESSORY (Z_PK, ZCONFIGUREDNAME, ZPROVIDEDNAME, ZROOM, ZMODELID) VALUES (5, 'Player A', 'Example', 1, ?)",
            (model_id(0, 5),),
        )
        connection.execute(
            "INSERT INTO ZMKFACCESSORY (Z_PK, ZPROVIDEDNAME, ZROOM, ZMODELID, ZUNIQUEIDENTIFIER) "
            "VALUES (6, 'Example Strip', 1, ?, 'SERIAL-0006')",
            (model_id(0, 6),),
        )
        connection.executemany(
            "INSERT INTO ZMKFSERVICE (Z_PK, ZNAME, ZPROVIDEDNAME, ZACCESSORY, ZSERVICETYPE, ZMODELID) VALUES (?, ?, ?, 6, ?, ?)",
            [
                (10, None, "Example Strip", ACCESSORY_INFORMATION, model_id(1, 10)),
                (11, "Outlet C", "Example", OUTLET, model_id(1, 11)),
                (12, "Outlet D", "Example", OUTLET, model_id(1, 12)),
            ],
        )
        connection.commit()
    finally:
        connection.close()


class DiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.older = root / "older.sqlite"
        self.newer = root / "newer.sqlite"
        build_store(self.older)
        add_real_columns(self.older)
        self.newer.write_bytes(self.older.read_bytes())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def diff_after(self, *statements: str) -> str:
        connection = sqlite3.connect(self.newer)
        try:
            for statement in statements:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()
        older = open_readonly(self.older)
        newer = open_readonly(self.newer)
        try:
            return format_diff(older, newer)
        finally:
            older.close()
            newer.close()

    def test_unchanged_snapshot_has_no_changes(self) -> None:
        self.assertEqual(self.diff_after(), "compared: previous -> current\nno changes\n")

    def test_speaker_without_a_unique_identifier_is_renamed(self) -> None:
        text = self.diff_after("UPDATE ZMKFACCESSORY SET ZCONFIGUREDNAME = 'Player B' WHERE Z_PK = 5")
        self.assertIn("accessories:\n  renamed: Player A -> Player B\n", text)

    def test_tile_rename_names_its_accessory(self) -> None:
        text = self.diff_after("UPDATE ZMKFSERVICE SET ZNAME = 'Outlet E' WHERE Z_PK = 12")
        self.assertEqual(
            text,
            "compared: previous -> current\nservices:\n  renamed: Outlet D -> Outlet E  accessory=Example Strip\n",
        )

    def test_accessory_rename_is_not_repeated_for_its_tile(self) -> None:
        text = self.diff_after(
            "UPDATE ZMKFACCESSORY SET ZCONFIGUREDNAME = 'Outlet F' WHERE Z_PK = 2",
            "UPDATE ZMKFSERVICE SET ZNAME = 'Outlet F' WHERE Z_PK = 2",
        )
        self.assertEqual(text.count("Outlet F"), 1)
        self.assertIn("renamed: Example Outlet -> Outlet F", text)
        self.assertNotIn("services:", text)

    def test_room_rename_is_one_line_and_moves_no_accessory(self) -> None:
        text = self.diff_after("UPDATE ZMKFROOM SET ZNAME = 'Zone Two' WHERE Z_PK = 1")
        self.assertEqual(text, "compared: previous -> current\nrooms:\n  renamed: Zone One -> Zone Two\n")

    def test_move_to_a_new_room(self) -> None:
        text = self.diff_after(
            f"INSERT INTO ZMKFROOM (Z_PK, ZNAME, ZMODELID) VALUES (2, 'Zone Two', x'{model_id(2, 2).hex()}')",
            "UPDATE ZMKFACCESSORY SET ZROOM = 2 WHERE Z_PK = 4",
        )
        self.assertIn("accessories:\n  moved: Lamp  Zone One -> Zone Two\n", text)
        self.assertIn("rooms:\n  added: Zone Two\n", text)

    def test_rows_match_by_model_id_when_row_ids_change(self) -> None:
        text = self.diff_after(
            "UPDATE ZMKFACCESSORY SET Z_PK = 40 WHERE Z_PK = 4",
            "UPDATE ZMKFACTION SET ZACCESSORY1 = 40 WHERE ZACCESSORY1 = 4",
            "UPDATE ZMKFACTION SET ZACCESSORY2 = 40 WHERE ZACCESSORY2 = 4",
        )
        self.assertEqual(text, "compared: previous -> current\nno changes\n")

    def test_edited_automation_lists_what_changed(self) -> None:
        text = self.diff_after(
            "UPDATE ZMKFEVENT SET ZFIREDATECOMPONENTS = x'" + clock_archive(17, 30).hex() + "' WHERE Z_PK = 10",
            "UPDATE ZMKFACTION SET ZTARGETVALUE = x'08' WHERE Z_PK = 1",
        )
        self.assertEqual(
            text,
            "compared: previous -> current\n"
            "automations:\n"
            "  edited: Evening\n"
            "    - trigger  16:00\n"
            "    - action  Example Outlet  service=Example Outlet  characteristic=Power State  target=true\n"
            "    + trigger  17:30\n"
            "    + action  Example Outlet  service=Example Outlet  characteristic=Power State  target=false\n",
        )

    def test_edited_scene_lists_what_changed(self) -> None:
        text = self.diff_after("UPDATE ZMKFACTION SET ZNATURALLIGHTINGENABLEDFIELD = 1 WHERE Z_PK = 5")
        self.assertEqual(
            text,
            "compared: previous -> current\n"
            "scenes:\n"
            "  edited: Away\n"
            "    - action  Lamp  adaptive lighting off\n"
            "    + action  Lamp  adaptive lighting on\n",
        )

    def test_rename_does_not_edit_the_automations_that_use_it(self) -> None:
        text = self.diff_after("UPDATE ZMKFACCESSORY SET ZCONFIGUREDNAME = 'Outlet F' WHERE Z_PK = 2")
        self.assertNotIn("edited", text)
        self.assertIn("renamed: Example Outlet -> Outlet F", text)

    def test_action_set_an_automation_owns_is_not_a_scene(self) -> None:
        text = self.diff_after(
            "INSERT INTO ZMKFACTIONSET (Z_PK, ZNAME, ZTYPE) "
            "VALUES (4, '00000000-0000-0000-0000-000000000004', 'HMActionSetTypeTriggerOwned')",
            "INSERT INTO ZMKFACTIONSET (Z_PK, ZNAME, ZTYPE) VALUES (5, 'Example Scene', 'HMActionSetTypeUserDefined')",
        )
        self.assertEqual(text, "compared: previous -> current\nscenes:\n  added: Example Scene\n")

    def test_disabled_automation_is_changed(self) -> None:
        text = self.diff_after("UPDATE ZMKFTRIGGER SET ZACTIVE = 0 WHERE Z_PK = 1")
        self.assertEqual(text, "compared: previous -> current\nautomations:\n  changed: Evening  enabled -> disabled\n")


if __name__ == "__main__":
    unittest.main()
