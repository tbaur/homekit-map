# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""The backup must not write the database it is copying."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from homekit_map.backup import run_backup, snapshot
from homekit_map.errors import SnapshotError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def live_hashes(directory: Path) -> dict[str, str]:
    return {path.name: sha256_file(path) for path in sorted(directory.iterdir())}


def make_database(directory: Path, *, wal: bool, hold_open: bool) -> tuple[Path, sqlite3.Connection | None]:
    directory.mkdir(parents=True)
    source = directory / "core.sqlite"
    connection = sqlite3.connect(source)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE accessory(id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO accessory VALUES (1, 'committed')")
    connection.commit()
    if hold_open:
        return source, connection
    connection.close()
    return source, None


class BackupTests(unittest.TestCase):
    def test_wal_backup_keeps_database_and_wal_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, writer = make_database(root / "Home Kit" / "live", wal=True, hold_open=True)
            assert writer is not None
            writer.execute("BEGIN")
            writer.execute("INSERT INTO accessory VALUES (2, 'not-committed')")
            before = live_hashes(source.parent)
            dest = root / "snapshot" / "core.sqlite"
            dest.parent.mkdir()
            mode, _archived = snapshot(source, dest)
            after = live_hashes(source.parent)
            self.assertEqual(mode, "backup")
            self.assertEqual(before["core.sqlite"], after["core.sqlite"])
            self.assertEqual(before["core.sqlite-wal"], after["core.sqlite-wal"])
            self.assertEqual(set(before), set(after))
            copied = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
            try:
                rows = copied.execute("SELECT id, name FROM accessory ORDER BY id").fetchall()
                integrity = copied.execute("PRAGMA integrity_check").fetchone()
            finally:
                copied.close()
            self.assertEqual(rows, [(1, "committed")])
            self.assertEqual(integrity, ("ok",))
            self.assertFalse((dest.parent / "core.sqlite.partial").exists())
            writer.rollback()
            writer.close()

    def test_byte_copy_does_not_create_wal_or_shm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _writer = make_database(root / "live", wal=False, hold_open=False)
            before = live_hashes(source.parent)
            self.assertNotIn("core.sqlite-wal", before)
            dest = root / "snapshot" / "core.sqlite"
            dest.parent.mkdir()
            mode, _archived = snapshot(source, dest)
            self.assertEqual(mode, "bytes")
            self.assertEqual(before, live_hashes(source.parent))

    def test_second_backup_replaces_the_snapshot_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _writer = make_database(root / "live", wal=False, hold_open=False)
            dest = root / "snapshot" / "core.sqlite"
            dest.parent.mkdir()
            snapshot(source, dest)
            before = live_hashes(source.parent)
            mode, archived = snapshot(source, dest)
            self.assertEqual(mode, "same")
            self.assertIsNotNone(archived)
            self.assertEqual(before, live_hashes(source.parent))
            self.assertTrue(dest.is_file())
            self.assertEqual(len(list((dest.parent / "versions").glob("*.sqlite"))), 1)
            mode, archived = snapshot(source, dest)
            self.assertEqual(mode, "same")
            self.assertIsNone(archived)
            self.assertEqual(len(list((dest.parent / "versions").glob("*.sqlite"))), 1)

    def test_refuses_to_write_inside_the_live_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, writer = make_database(root / "live", wal=True, hold_open=True)
            assert writer is not None
            before = live_hashes(source.parent)
            with self.assertRaises(SnapshotError):
                snapshot(source, source.parent / "stolen.sqlite")
            with self.assertRaises(SnapshotError):
                snapshot(source, source)
            self.assertEqual(before, live_hashes(source.parent))
            self.assertFalse((source.parent / "stolen.sqlite").exists())
            writer.close()

    def test_refuses_the_live_folder_spelled_in_another_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, writer = make_database(root / "Live", wal=True, hold_open=True)
            assert writer is not None
            other_case = root / "LIVE"
            if not other_case.exists():
                writer.close()
                self.skipTest("this filesystem is case-sensitive")
            before = live_hashes(source.parent)
            inode = source.stat().st_ino
            for dest in (other_case / "copy.sqlite", other_case / "CORE.sqlite", other_case / "new" / "copy.sqlite"):
                with self.subTest(dest=dest.name), self.assertRaises(SnapshotError):
                    snapshot(source, dest)
            self.assertEqual(run_backup(source, other_case / "CORE.sqlite"), 1)
            self.assertEqual(before, live_hashes(source.parent))
            self.assertEqual(inode, source.stat().st_ino)
            writer.close()

    def test_refuses_to_follow_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _writer = make_database(root / "live", wal=False, hold_open=False)
            before = live_hashes(source.parent)
            link = root / "link.sqlite"
            link.symlink_to(source)
            with self.assertRaises(SnapshotError):
                snapshot(link, root / "snapshot" / "other.sqlite")
            self.assertEqual(before, live_hashes(source.parent))

    def test_discards_partial_when_the_source_is_not_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "live" / "core.sqlite"
            source.parent.mkdir()
            source.write_text("not a database", encoding="utf-8")
            dest = root / "snapshot" / "core.sqlite"
            dest.parent.mkdir()
            with self.assertRaises(SnapshotError):
                snapshot(source, dest)
            self.assertFalse(dest.exists())
            self.assertFalse((dest.parent / "core.sqlite.partial").exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "not a database")

    def test_command_creates_the_destination_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, writer = make_database(root / "live", wal=True, hold_open=True)
            assert writer is not None
            before = live_hashes(source.parent)
            dest = root / "data" / "core.sqlite"
            status = run_backup(source, dest)
            self.assertEqual(status, 0)
            self.assertTrue(dest.is_file())
            self.assertEqual(before["core.sqlite"], live_hashes(source.parent)["core.sqlite"])
            self.assertEqual(before["core.sqlite-wal"], live_hashes(source.parent)["core.sqlite-wal"])
            writer.close()

    def test_command_refuses_a_missing_database_folder_inside_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _writer = make_database(root / "live", wal=False, hold_open=False)
            before = live_hashes(source.parent)
            status = run_backup(source, source.parent / "nested" / "core.sqlite")
            self.assertEqual(status, 1)
            self.assertEqual(before, live_hashes(source.parent))
            self.assertFalse((source.parent / "nested").exists())

    def test_changed_backup_keeps_one_version_and_skips_a_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _writer = make_database(root / "live", wal=False, hold_open=False)
            dest = root / "snapshot" / "core.sqlite"
            dest.parent.mkdir()
            _mode, archived = snapshot(source, dest)
            self.assertIsNone(archived)
            self.assertFalse((dest.parent / "versions").exists())
            writer = sqlite3.connect(source)
            writer.execute("UPDATE accessory SET name = 'changed' WHERE id = 1")
            writer.commit()
            writer.close()
            before = live_hashes(source.parent)
            mode, archived = snapshot(source, dest)
            self.assertEqual(mode, "bytes")
            self.assertIsNotNone(archived)
            self.assertEqual(before, live_hashes(source.parent))
            self.assertEqual(len(list((dest.parent / "versions").glob("*.sqlite"))), 1)
            mode, archived = snapshot(source, dest)
            self.assertEqual(mode, "same")
            self.assertIsNone(archived)
            self.assertEqual(len(list((dest.parent / "versions").glob("*.sqlite"))), 1)
            self.assertEqual(before, live_hashes(source.parent))


if __name__ == "__main__":
    unittest.main()
