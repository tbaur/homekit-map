# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Stored characteristic targets and clock archives render as short text."""

from __future__ import annotations

import plistlib
import struct
import unittest
from pathlib import Path

from homekit_map.decode import decode_clock, decode_target
from homekit_map.paths import is_within, repo_root


def clock_archive(hour: int, minute: int) -> bytes:
    return plistlib.dumps({"$objects": ["$null", {"NS.hour": hour, "NS.minute": minute}]}, fmt=plistlib.FMT_BINARY)


class DecodeTargetTest(unittest.TestCase):
    def test_none_stays_none(self) -> None:
        self.assertIsNone(decode_target(None))

    def test_single_byte_bools(self) -> None:
        self.assertEqual(decode_target(b"\x08"), "false")
        self.assertEqual(decode_target(b"\x09"), "true")
        self.assertEqual(decode_target(b"\x01", "bool"), "false")
        self.assertEqual(decode_target(b"\x02", "bool"), "true")

    def test_bool_format_reads_a_number(self) -> None:
        self.assertEqual(decode_target(b"\x30\x01", "bool"), "true")
        self.assertEqual(decode_target(b"\x30\x00", "bool"), "false")

    def test_bool_format_with_unknown_bytes_shows_hex(self) -> None:
        self.assertEqual(decode_target(b"\xff\xff", "bool"), "0xffff")

    def test_tagged_numbers(self) -> None:
        self.assertEqual(decode_target(b"\x30\x2a", units="percentage"), "42%")
        self.assertEqual(decode_target(b"\x31" + (300).to_bytes(2, "little"), units="arcdegrees"), "300°")
        self.assertEqual(decode_target(b"\x35" + struct.pack("<f", 50.0)), "50")
        self.assertEqual(decode_target(b"\x36" + struct.pack("<d", 2.5)), "2.5")

    def test_single_byte_with_numeric_format(self) -> None:
        self.assertEqual(decode_target(b"\x08", "uint8"), "8")

    def test_plist_value_is_json(self) -> None:
        self.assertEqual(decode_target(plistlib.dumps("Example")), '"Example"')
        self.assertEqual(decode_target(plistlib.dumps({"a": [1, 2]})), '{"a":[1,2]}')

    def test_unreadable_bytes_show_hex(self) -> None:
        self.assertEqual(decode_target(b"\x99\x00\x01"), "0x990001")


class DecodeClockTest(unittest.TestCase):
    def test_reads_hour_and_minute(self) -> None:
        self.assertEqual(decode_clock(clock_archive(7, 30)), (7, 30))

    def test_empty_or_unreadable_is_none(self) -> None:
        self.assertIsNone(decode_clock(None))
        self.assertIsNone(decode_clock(b"not a plist"))
        self.assertIsNone(decode_clock(plistlib.dumps(["no", "objects"])))
        self.assertIsNone(decode_clock(plistlib.dumps({"$objects": [{"NS.day": 1}]})))


class PathsTest(unittest.TestCase):
    def test_repo_root_holds_pyproject(self) -> None:
        self.assertTrue((repo_root() / "pyproject.toml").is_file())

    def test_is_within(self) -> None:
        base = Path("/home/user/example")
        self.assertTrue(is_within(base, base))
        self.assertTrue(is_within(base / "core.sqlite", base))
        self.assertFalse(is_within(Path("/home/user/example-other"), base))


if __name__ == "__main__":
    unittest.main()
