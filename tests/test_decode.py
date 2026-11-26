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

from homekit_map.decode import decode_clock, decode_number, decode_target, duration_text
from homekit_map.paths import is_within, repo_root

CONTACT_STATE = bytes.fromhex("0000006A0000100080000026BB765291")
LEAK_DETECTED = bytes.fromhex("000000700000100080000026BB765291")
SWITCH_EVENT = bytes.fromhex("000000730000100080000026BB765291")


def clock_archive(hour: int, minute: int) -> bytes:
    return plistlib.dumps({"$objects": ["$null", {"NS.hour": hour, "NS.minute": minute}]}, fmt=plistlib.FMT_BINARY)


class DecodeTargetTest(unittest.TestCase):
    def test_none_stays_none(self) -> None:
        self.assertIsNone(decode_target(None))

    def test_one_is_true_and_two_is_false(self) -> None:
        self.assertEqual(decode_target(b"\x01"), "true")
        self.assertEqual(decode_target(b"\x02"), "false")
        self.assertEqual(decode_target(b"\x01", "bool"), "true")
        self.assertEqual(decode_target(b"\x02", "bool"), "false")

    def test_small_integers_start_at_eight(self) -> None:
        self.assertEqual(decode_target(b"\x08"), "0")
        self.assertEqual(decode_target(b"\x09"), "1")
        self.assertEqual(decode_target(b"\x2f"), "39")
        self.assertEqual(decode_target(b"\x08", "uint8"), "0")
        self.assertEqual(decode_target(b"\x12", "int", "percentage"), "10%")
        self.assertEqual(decode_target(b"\x21", "uint8", "percentage"), "25%")

    def test_bool_format_reads_zero_and_one(self) -> None:
        self.assertEqual(decode_target(b"\x08", "bool"), "false")
        self.assertEqual(decode_target(b"\x09", "bool"), "true")
        self.assertEqual(decode_target(b"\x30\x01", "bool"), "true")
        self.assertEqual(decode_target(b"\x30\x00", "bool"), "false")
        self.assertEqual(decode_target(b"\x36" + struct.pack("<d", 1.0), "bool"), "true")

    def test_bool_format_with_unknown_bytes_shows_hex(self) -> None:
        self.assertEqual(decode_target(b"\xff\xff", "bool"), "0xffff")

    def test_tagged_numbers(self) -> None:
        self.assertEqual(decode_target(b"\x30\x2a", units="percentage"), "42%")
        self.assertEqual(decode_target(b"\x30\x64", "int", "percentage"), "100%")
        self.assertEqual(decode_target(b"\x31" + (300).to_bytes(2, "little"), units="arcdegrees"), "300°")
        self.assertEqual(decode_target(b"\x32" + (70000).to_bytes(4, "little")), "70000")
        self.assertEqual(decode_target(b"\x33" + (2**40).to_bytes(8, "little")), str(2**40))
        self.assertEqual(decode_target(b"\x35" + struct.pack("<f", 50.0)), "50")
        self.assertEqual(decode_target(b"\x36" + struct.pack("<d", 2.5)), "2.5")

    def test_null_and_short_string(self) -> None:
        self.assertEqual(decode_target(b"\x04"), "null")
        self.assertEqual(decode_target(b"\x47Example"), '"Example"')

    def test_a_tag_with_the_wrong_length_is_hex(self) -> None:
        self.assertEqual(decode_target(b"\x30"), "0x30")
        self.assertEqual(decode_target(b"\x09\x00"), "0x0900")

    def test_known_states_read_as_words(self) -> None:
        self.assertEqual(decode_target(b"\x08", "uint8", None, SWITCH_EVENT), "single press")
        self.assertEqual(decode_target(b"\x09", "uint8", None, CONTACT_STATE), "open")
        self.assertEqual(decode_target(b"\x09", "uint8", None, LEAK_DETECTED), "true")
        self.assertEqual(decode_target(b"\x01", "uint8", None, LEAK_DETECTED), "true")
        self.assertEqual(decode_target(b"\x09", "uint8", None, bytes(16)), "1")

    def test_units(self) -> None:
        self.assertEqual(decode_target(b"\x35" + struct.pack("<f", 410.996), "float", "lux"), "411 lux")
        self.assertEqual(decode_target(b"\x36" + struct.pack("<d", 21.5), "float", "celsius"), "21.5°C")
        self.assertEqual(decode_target(b"\x35" + struct.pack("<f", 100000.0), "float", "lux"), "100000 lux")
        self.assertEqual(decode_target(b"\x36" + struct.pack("<d", 12345.6), "float"), "12346")

    def test_numbers_for_thresholds(self) -> None:
        self.assertEqual(decode_number(b"\x30\x64"), 100)
        self.assertEqual(decode_number(b"\x35" + struct.pack("<f", 2.5)), 2.5)
        self.assertIsNone(decode_number(b"\x01"))
        self.assertIsNone(decode_number(None))

    def test_durations(self) -> None:
        self.assertEqual(duration_text(30), "30 seconds")
        self.assertEqual(duration_text(60), "1 minute")
        self.assertEqual(duration_text(3600), "1 hour")
        self.assertEqual(duration_text(5400), "1 hour 30 minutes")
        self.assertEqual(duration_text(7200), "2 hours")

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
