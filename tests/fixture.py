# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""A tiny HomeKit-shaped database. Names here are placeholders."""

from __future__ import annotations

import plistlib
import sqlite3
from pathlib import Path

POWER_STATE = bytes.fromhex("000000250000100080000026BB765291")
SWITCH_EVENT = bytes.fromhex("000000730000100080000026BB765291")
LIGHT_LEVEL = bytes.fromhex("0000006B0000100080000026BB765291")
ACCESSORY_INFORMATION = bytes.fromhex("0000003E0000100080000026BB765291")
OUTLET = bytes.fromhex("000000470000100080000026BB765291")


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


def kind(name: str) -> dict[str, object]:
    return {"$classname": name}


def compound(compound_type: int, parts: list[dict[str, object]]) -> dict[str, object]:
    return {"$class": kind("NSCompoundPredicate"), "NSCompoundPredicateType": compound_type, "NSSubpredicates": parts}


def clock_archive(hour: int, minute: int) -> bytes:
    """An NSDateComponents archive with an hour and a minute."""
    return archive({"$class": kind("NSDateComponents"), "NS.hour": hour, "NS.minute": minute})


def build_store(path: Path) -> None:
    """Create a snapshot with two outlets, one rename, one scene, and one shortcut."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
            CREATE TABLE ZMKFROOM (Z_PK INTEGER, ZNAME TEXT);
            CREATE TABLE ZMKFACCESSORY (
                Z_PK INTEGER, ZCONFIGUREDNAME TEXT, ZPROVIDEDNAME TEXT,
                ZMANUFACTURER TEXT, ZMODEL TEXT, ZROOM INTEGER
            );
            CREATE TABLE ZMKFSERVICE (
                Z_PK INTEGER, ZNAME TEXT, ZEXPECTEDCONFIGUREDNAME TEXT, ZACCESSORY INTEGER,
                ZPROVIDEDNAME TEXT, ZINSTANCEID INTEGER, ZSERVICETYPE BLOB
            );
            CREATE TABLE ZMKFCHARACTERISTIC (
                Z_PK INTEGER, ZSERVICE INTEGER, ZMANUFACTURERDESCRIPTION TEXT,
                ZFORMAT TEXT, ZUNITS TEXT, ZINSTANCEID INTEGER, ZTYPE BLOB,
                ZMINIMUMVALUE REAL, ZMAXIMUMVALUE REAL
            );
            CREATE TABLE ZMKFTRIGGER (
                Z_PK INTEGER, Z_ENT INTEGER, ZCONFIGUREDNAME TEXT, ZNAME TEXT,
                ZACTIVE INTEGER, ZRECURRENCEDAYS INTEGER
            );
            CREATE TABLE ZMKFACTIONSET (Z_PK INTEGER, ZNAME TEXT, ZTYPE TEXT);
            CREATE TABLE ZMKFACTION (
                Z_PK INTEGER, Z_ENT INTEGER, ZACTIONSET INTEGER, ZACCESSORY1 INTEGER,
                ZSERVICE INTEGER, ZCHARACTERISTICID INTEGER, ZTARGETVALUE BLOB, ZDATA BLOB,
                ZACCESSORY2 INTEGER, ZNATURALLIGHTINGENABLEDFIELD INTEGER, ZACCESSORY INTEGER
            );
            CREATE TABLE ZMKFEVENT (
                Z_PK INTEGER, Z_ENT INTEGER, ZTRIGGER INTEGER,
                ZSERVICE INTEGER, ZCHARACTERISTICID INTEGER,
                ZSIGNIFICANTEVENT TEXT, ZOFFSETSECONDS INTEGER,
                ZDURATION REAL, ZPRESENCETYPE TEXT, ZFIREDATECOMPONENTS BLOB,
                ZENDEVENT INTEGER, ZEVENTVALUE BLOB, ZMIN BLOB, ZMAX BLOB
            );
            CREATE TABLE Z_30TRIGGERS_ (Z_30ACTIONSETS_ INTEGER, Z_99TRIGGERS_ INTEGER);
            CREATE TABLE Z_26ACTIONMEDIAPLAYBACKS_ (Z_26ACCESSORIES_ INTEGER, Z_38ACTIONMEDIAPLAYBACKS_ INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO Z_PRIMARYKEY VALUES (?, ?)",
            [
                (10, "MKFCharacteristicWriteAction"),
                (11, "MKFShortcutAction"),
                (38, "MKFMediaPlaybackAction"),
                (39, "MKFNaturalLightingAction"),
                (20, "MKFEventTrigger"),
                (21, "MKFTimerTrigger"),
                (30, "MKFActionSet"),
                (50, "MKFCharacteristicValueEvent"),
                (90, "MKFCalendarEvent"),
            ],
        )
        connection.execute("INSERT INTO ZMKFROOM VALUES (1, 'Zone One')")
        connection.executemany(
            "INSERT INTO ZMKFACCESSORY VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, None, "Outlet A", "Example", "Strip", 1),
                (2, "Example Outlet", "Outlet B", "Example", "Strip", 1),
                (3, None, "Outlet A S1", "Example", "Strip", 1),
                (4, None, "Lamp", "Example", "Bulb", 1),
            ],
        )
        connection.executemany(
            "INSERT INTO ZMKFSERVICE VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Outlet A", "Outlet A", 1, "Outlet A", 9, None),
                (2, "Example Outlet", None, 2, "Outlet B", 9, None),
                (3, "Outlet A S1", "Outlet A S1", 3, "Outlet A S1", 9, None),
            ],
        )
        connection.executemany(
            "INSERT INTO ZMKFCHARACTERISTIC VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 2, "Power State", "bool", None, 1, POWER_STATE, None, None),
                (2, 1, "Power State", "bool", None, 1, POWER_STATE, None, None),
            ],
        )
        connection.executemany(
            "INSERT INTO ZMKFTRIGGER VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 20, "Evening", "Evening", 1, 127),
                (2, 20, "Morning", "Morning", 1, 127),
            ],
        )
        connection.executemany(
            "INSERT INTO ZMKFACTIONSET VALUES (?, ?, ?)",
            [
                (1, "", "HMActionSetTypeTriggerOwned"),
                (2, "", "HMActionSetTypeTriggerOwned"),
                (3, "Away", "HMActionSetTypeUserDefined"),
            ],
        )
        connection.executemany(
            "INSERT INTO Z_30TRIGGERS_ VALUES (?, ?)",
            [(1, 1), (2, 2)],
        )
        connection.executemany(
            "INSERT INTO ZMKFACTION VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 10, 1, 2, 2, 1, b"\x09", None, None, None, None),
                (2, 10, 3, 2, 2, 999, b"\x02", None, None, None, None),
                (3, 11, 2, None, None, None, None, b"see Outlet A S1 now", None, None, None),
                (4, 10, 1, 4, None, None, b"\x08", None, None, None, None),
                (5, 39, 3, None, None, None, None, None, 4, 0, None),
            ],
        )
        connection.executemany(
            "INSERT INTO ZMKFEVENT VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 50, 2, 1, 1, None, None, None, None, None, 0, b"\x01", None, None),
                (10, 90, 1, None, None, None, None, None, None, clock_archive(16, 0), 0, None, None, None),
            ],
        )
        connection.commit()
    finally:
        connection.close()
