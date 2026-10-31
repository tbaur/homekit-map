# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""A tiny HomeKit-shaped database. Names here are placeholders."""

from __future__ import annotations

import sqlite3
from pathlib import Path


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
                Z_PK INTEGER, ZNAME TEXT, ZEXPECTEDCONFIGUREDNAME TEXT, ZACCESSORY INTEGER
            );
            CREATE TABLE ZMKFCHARACTERISTIC (
                Z_PK INTEGER, ZSERVICE INTEGER, ZMANUFACTURERDESCRIPTION TEXT,
                ZFORMAT TEXT, ZUNITS TEXT, ZINSTANCEID INTEGER
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
                ZDURATION REAL, ZPRESENCETYPE TEXT, ZFIREDATECOMPONENTS BLOB
            );
            CREATE TABLE Z_30TRIGGERS_ (Z_30ACTIONSETS_ INTEGER, Z_99TRIGGERS_ INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO Z_PRIMARYKEY VALUES (?, ?)",
            [
                (10, "MKFCharacteristicWriteAction"),
                (11, "MKFShortcutAction"),
                (39, "MKFNaturalLightingAction"),
                (20, "MKFEventTrigger"),
                (21, "MKFTimerTrigger"),
                (30, "MKFActionSet"),
                (50, "MKFCharacteristicValueEvent"),
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
            "INSERT INTO ZMKFSERVICE VALUES (?, ?, ?, ?)",
            [
                (1, "Outlet A", "Outlet A", 1),
                (2, "Example Outlet", None, 2),
                (3, "Outlet A S1", "Outlet A S1", 3),
            ],
        )
        connection.execute(
            "INSERT INTO ZMKFCHARACTERISTIC VALUES (1, 2, 'Power State', 'bool', NULL, 1)"
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
                (2, 10, 3, 2, 2, 999, b"\x08", None, None, None, None),
                (3, 11, 2, None, None, None, None, b"see Outlet A S1 now", None, None, None),
                (4, 10, 1, 4, None, None, b"\x08", None, None, None, None),
                (5, 39, 3, None, None, None, None, None, 4, 0, None),
            ],
        )
        connection.execute(
            "INSERT INTO ZMKFEVENT VALUES (1, 50, 2, 1, 1, NULL, NULL, NULL, NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()
